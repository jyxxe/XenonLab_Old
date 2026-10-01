import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function port() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', resolve).once('error', reject));
  const value = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return value;
}

async function waitFor(fn, label, timeout = 20000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    try {
      const result = await fn();
      if (result) return result;
    } catch {
      /* keep polling */
    }
    await sleep(80);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true);
  return new Promise(resolve => {
    const onExit = () => {
      clearTimeout(timer);
      resolve(true);
    };
    const timer = setTimeout(() => {
      child.off('exit', onExit);
      resolve(false);
    }, timeoutMs);
    child.once('exit', onExit);
  });
}

async function stopProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  if (await waitForExit(child, 3000)) return;
  child.kill('SIGKILL');
  if (!await waitForExit(child, 3000)) throw new Error('Chrome did not exit after SIGKILL');
}

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.id = 0;
    this.pending = new Map();
    this.listeners = new Map();
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message));
        else pending.resolve(message.result);
        return;
      }
      for (const listener of this.listeners.get(message.method) || []) {
        Promise.resolve(listener(message.params)).catch(() => undefined);
      }
    });
  }
  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, {once: true});
      socket.addEventListener('error', reject, {once: true});
    });
    return new CDP(socket);
  }
  on(method, listener) {
    if (!this.listeners.has(method)) this.listeners.set(method, []);
    this.listeners.get(method).push(listener);
  }
  call(method, params = {}) {
    const id = ++this.id;
    this.socket.send(JSON.stringify({id, method, params}));
    return new Promise((resolve, reject) => this.pending.set(id, {resolve, reject}));
  }
  async evaluate(expression) {
    const response = await this.call('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text || 'Browser evaluation failed');
    return response.result?.value;
  }
}

test('Session resilience: cached tokens, recoverable rate limits, no wrongful logout', async t => {
  const chromePath = process.env.CHROME_BIN || ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find(file => existsSync(file));
  if (!chromePath) {
    t.skip('Chrome/Chromium unavailable; run with CHROME_BIN=/path/to/chrome');
    return;
  }
  const portNumber = await port();
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: portNumber, strictPort: true, watch: null}});
  await server.listen();
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-session-acceptance-'));
  const chrome = spawn(
    chromePath,
    [
      '--headless=new',
      '--no-first-run',
      '--no-default-browser-check',
      `--user-data-dir=${path.join(tempDir, 'profile')}`,
      '--remote-debugging-port=0',
      '--remote-debugging-address=127.0.0.1',
      '--remote-allow-origins=*',
      'about:blank',
    ],
    {stdio: 'ignore'},
  );
  let cdp;
  try {
    const endpoint = await waitFor(async () => {
      const raw = await fs.readFile(path.join(tempDir, 'profile', 'DevToolsActivePort'), 'utf8').catch(() => null);
      return raw ? {port: Number(raw.split(/\r?\n/)[0])} : null;
    }, 'Chrome DevTools endpoint');
    const target = await (await fetch(`http://127.0.0.1:${endpoint.port}/json/new?about%3Ablank`, {method: 'PUT'})).json();
    cdp = await CDP.connect(target.webSocketDebuggerUrl);
    await cdp.call('Page.enable');
    await cdp.call('Runtime.enable');

    const state = {mode: 'ok', tokenCalls: 0, dataCalls: 0, delayToken: false, delayedTokenRequestId: null, returnTable: false, rawMode: 'ok', rawCalls: 0, rawUploadCalls: 0, delayedRawRequestId: null, delayRaw: false};
    const preferences = {
      theme: 'dark',
      accent: 'blue',
      wallpaper: 'aurora',
      timezone: 'UTC',
      widgets: [],
      dock_size: 'medium',
      dock_autohide: false,
      restore_windows: true,
      animations: true,
      reduced_motion: false,
    };
    const fulfil = (requestId, status, body, headers = []) =>
      cdp.call('Fetch.fulfillRequest', {
        requestId,
        responseCode: status,
        responseHeaders: [{name: 'Content-Type', value: 'application/json'}, ...headers],
        body: Buffer.from(JSON.stringify(body)).toString('base64'),
      });
    const fulfilRaw = (requestId, status, body, contentType = 'text/plain', headers = []) =>
      cdp.call('Fetch.fulfillRequest', {
        requestId,
        responseCode: status,
        responseHeaders: [{name: 'Content-Type', value: contentType}, ...headers],
        body: Buffer.from(body).toString('base64'),
      });
    const errorBody = (code, message) => ({error: {code, message}});

    cdp.on('Fetch.requestPaused', async ({requestId, request}) => {
      const url = new URL(request.url);
      if (url.pathname === '/health/ready') {
        return fulfil(requestId, 200, {status: 'ready', service: 'desktop-web'});
      }
      if (url.pathname === '/api/v1/attachments/attachment-1/content') {
        state.rawCalls += 1;
        if(state.delayRaw){state.delayedRawRequestId=requestId;return;}
        if(state.rawMode==='gateway')return fulfilRaw(requestId,502,'<html>attachment gateway unavailable</html>','text/html',[{name:'Retry-After',value:'1'}]);
        if(state.rawMode==='network')return cdp.call('Fetch.failRequest',{requestId,errorReason:'Failed'});
        return fulfilRaw(requestId,200,'raw-fixture-content','application/octet-stream');
      }
      if (url.pathname === '/api/v1/attachments' && request.method === 'POST') {
        state.rawUploadCalls += 1;
        return fulfilRaw(requestId,502,'<html>upload gateway unavailable</html>','text/html',[{name:'Retry-After',value:'1'}]);
      }
      const data = () => {
        state.dataCalls += 1;
        if (state.mode === 'rate') {
          return fulfil(requestId, 429, errorBody('AUTHORIZATION_RATE_LIMITED', 'request rate limit exceeded'), [{name: 'Retry-After', value: '2'}]);
        }
        if (state.mode === 'error') return fulfil(requestId, 500, errorBody('INTERNAL_ERROR', 'internal service error'));
        if (state.mode === 'gateway') return fulfilRaw(requestId, 502, '<html>upstream unavailable</html>', 'text/html');
        if (state.mode === 'network') return cdp.call('Fetch.failRequest', {requestId, errorReason: 'Failed'});
        if (state.mode === 'revoked') return fulfil(requestId, 401, errorBody('DESKTOP_ACCESS_REVOKED', 'desktop permission revoked'));
        if (state.returnTable&&url.pathname==='/api/v1/tables') return fulfil(requestId,200,{data:[{id:'recovered-table',name:'部署验收',path:'/示例/平台'}],meta:{request_id:'fixture',next_cursor:null}});
        return fulfil(requestId, 200, {data: [], meta: {request_id: 'fixture', next_cursor: null}});
      };
      if (url.pathname === '/api/v1/auth/session/token') {
        state.tokenCalls += 1;
        if (state.delayToken) { state.delayedTokenRequestId=requestId; return; }
        if (state.mode === 'revoked') return fulfil(requestId, 401, errorBody('DESKTOP_ACCESS_REVOKED', 'desktop permission revoked'));
        return fulfil(requestId, 200, {token: `access-${state.tokenCalls}`, expires: 300});
      }
      if (url.pathname === '/api/v1/apps') return fulfil(requestId, 200, {data: [], meta: {request_id: 'fixture'}});
      if (url.pathname === '/api/v1/tables' && url.searchParams.get('limit') === '100') {
        return fulfil(requestId,200,{data:[{id:'raw-fixture',path:'/示例/平台',name:'附件测试',table_revision:1,schema_revision:1}],meta:{request_id:'fixture',next_cursor:null}});
      }
      if (url.pathname === '/api/v1/tables/raw-fixture/schema') {
        return fulfil(requestId,200,{data:{columns:[{name:'title',type:'text',nullable:true,default_value:null}],table_revision:1,schema_revision:1},meta:{request_id:'fixture'}});
      }
      if (url.pathname === '/api/v1/tables/raw-fixture/rows' && request.method === 'GET') {
        return fulfil(requestId,200,{data:[{_row_id:'raw-row',_revision:1,_created_at:'2026-01-01T00:00:00Z',_updated_at:'2026-01-01T00:00:00Z',_deleted_at:null,values:{title:'附件读取测试'}}],meta:{request_id:'fixture',next_cursor:null}});
      }
      if (url.pathname === '/api/v1/tables/raw-fixture/rows/raw-row/attachments') {
        return fulfil(requestId,200,{data:[{attachment_id:'attachment-1',row_id:'raw-row',filename:'fixture.txt',media_type:'text/plain',size:19,created_at:'2026-01-01T00:00:00Z',metadata:{}}],meta:{request_id:'fixture',next_cursor:null}});
      }
      if (url.pathname === '/api/v1/users/me') {
        return fulfil(requestId, 200, {data: {subject: 'fixture-admin', kind: 'human', scopes: ['desktop:read', 'xelibrary:read', 'xelibrary:attach', 'dev:read']}, meta: {request_id: 'fixture'}});
      }
      if (url.pathname === '/api/v1/users/me/layout') return fulfil(requestId, 200, {data: {dock: [], windows: []}, meta: {request_id: 'fixture'}});
      if (url.pathname === '/api/v1/users/me/preferences') return fulfil(requestId, 200, {data: preferences, meta: {request_id: 'fixture'}});
      return data();
    });
    await cdp.call('Fetch.enable', {patterns: [{urlPattern: '*api/v1*'}, {urlPattern: '*health*'}]});
    await cdp.call('Page.navigate', {url: `http://127.0.0.1:${portNumber}/`});

    const bannerPhase = () => cdp.evaluate("document.querySelector('[data-testid=session-status-banner]')?.dataset.phase || null");
    const statusPhase = () => cdp.evaluate("document.querySelector('output[data-testid=session-status]')?.dataset.phase || null");
    const workspaceVisible = () => cdp.evaluate("Boolean(document.querySelector('nav.dock') && !document.body.innerText.includes('登录 XeonoLab'))");

    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('nav.dock'))"), 'connected desktop');
    await waitFor(() => cdp.evaluate("document.querySelector('output[data-testid=session-status]')?.dataset.phase === 'connected'"), 'connected session phase');
    const tokensAfterBootstrap = state.tokenCalls;
    assert.ok(tokensAfterBootstrap >= 1 && tokensAfterBootstrap <= 2, `bootstrap refreshes the token once (${tokensAfterBootstrap})`);

    // Open two apps: the burst must share one cached token, not refresh per request.
    await cdp.evaluate("[...document.querySelectorAll('nav.dock button')].find(button=>button.title==='知识库')?.click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.knowledge'))"), 'knowledge app');
    await cdp.evaluate("[...document.querySelectorAll('nav.dock button')].find(button=>button.title==='系统状态')?.click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.dashboard'))"), 'system app');
    assert.equal(state.tokenCalls, tokensAfterBootstrap, 'browsing does not refresh the access token per request');

    // Attachment downloads use apiRaw. A transient response must queue and replay
    // the original GET so the component receives the recovered Response body.
    await cdp.evaluate(`(() => {
      window.__downloadedBlobSizes=[];
      URL.createObjectURL=blob=>{window.__downloadedBlobSizes.push(blob.size);return 'blob:fixture-download'};
      URL.revokeObjectURL=()=>{};
      [...document.querySelectorAll('nav.dock button')].find(button=>button.title==='知识库')?.click();
    })()`);
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-welcome-list button'))"), 'fixture table in library');
    await cdp.evaluate("document.querySelector('.library-welcome-list button').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('tr[data-row-id=raw-row]'))"), 'fixture row');
    await cdp.evaluate("document.querySelector('tr[data-row-id=raw-row]').click()");
    await waitFor(() => cdp.evaluate("Boolean([...document.querySelectorAll('.attachment-panel button')].find(button=>button.innerText.trim()==='鉴权下载'))"), 'attachment download action');
    state.rawMode='gateway';
    const rawGatewayCalls=state.rawCalls;
    await cdp.evaluate("[...document.querySelectorAll('.attachment-panel button')].find(button=>button.innerText.trim()==='鉴权下载').click()");
    await waitFor(() => bannerPhase().then(phase => phase === 'retrying'), 'apiRaw gateway retry banner');
    await sleep(350);
    assert.equal(state.rawCalls,rawGatewayCalls+1,'apiRaw does not retry before Retry-After');
    state.rawMode='ok';
    await waitFor(() => state.rawCalls===rawGatewayCalls+2&&cdp.evaluate('window.__downloadedBlobSizes.length===1'),'apiRaw 502 GET replayed after Retry-After');
    assert.equal(await cdp.evaluate('window.__downloadedBlobSizes[0]'),Buffer.byteLength('raw-fixture-content'),'the original download receives the replayed body');

    state.rawMode='network';
    const rawNetworkCalls=state.rawCalls;
    await cdp.evaluate("[...document.querySelectorAll('.attachment-panel button')].find(button=>button.innerText.trim()==='鉴权下载').click()");
    await waitFor(() => bannerPhase().then(phase => phase === 'retrying'), 'apiRaw network retry banner');
    state.rawMode='ok';
    await cdp.evaluate("document.querySelector('[data-testid=session-retry]')?.click()");
    await waitFor(() => state.rawCalls===rawNetworkCalls+2&&cdp.evaluate('window.__downloadedBlobSizes.length===2'),'apiRaw network GET replayed by Retry');

    const rawUploadCalls=state.rawUploadCalls;
    await cdp.evaluate(`(() => {
      const input=document.querySelector('.attachment-panel input[type=file]');
      const transfer=new DataTransfer();transfer.items.add(new File(['upload-fixture'],'upload.txt',{type:'text/plain'}));
      input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));
    })()`);
    await waitFor(() => state.rawUploadCalls===rawUploadCalls+1, 'attachment upload attempted once');
    await waitFor(() => bannerPhase().then(phase => phase === 'retrying'), 'upload 502 recovery banner');
    await waitFor(() => bannerPhase().then(phase => phase===null), 'upload cooldown settles without replaying the write');
    assert.equal(state.rawUploadCalls,rawUploadCalls+1,'unsafe attachment upload is never automatically replayed');

    // Rate limiting keeps the workspace and shows a recoverable banner.
    const tokensBeforeRateLimit=state.tokenCalls;
    state.mode = 'rate';
    await cdp.evaluate("[...document.querySelectorAll('.dashboard-actions button')].find(button=>button.innerText.trim()==='刷新')?.click()");
    await waitFor(() => bannerPhase().then(phase => phase === 'retrying'), 'retryable rate-limit banner');
    assert.equal(await workspaceVisible(), true, 'workspace and windows are kept during rate limiting');
    assert.equal(await statusPhase(), 'retrying');
    const rateLimitMessage = await cdp.evaluate("document.querySelector('[data-testid=session-status-banner]').innerText");
    assert.match(rateLimitMessage, /工作区/);
    const limitedDataCalls=state.dataCalls;
    assert.equal(await cdp.evaluate("document.querySelector('[data-testid=session-retry]').disabled"),true,'Retry-After disables an early manual retry');
    const tokensDuringRateLimit = state.tokenCalls;
    assert.equal(tokensDuringRateLimit, tokensBeforeRateLimit, 'rate limiting does not trigger a token storm');

    // Background recovery waits for Retry-After and replays the failed reads.
    state.mode = 'ok';
    state.returnTable=true;
    await cdp.evaluate("[...document.querySelectorAll('.dashboard-actions button')].find(button=>button.innerText.trim()==='刷新')?.click()");
    await sleep(400);
    assert.equal(state.dataCalls,limitedDataCalls,'new browsing requests are held during Retry-After');
    await sleep(700);
    assert.equal(state.dataCalls,limitedDataCalls,'no data retry is sent before Retry-After expires');
    await waitFor(() => statusPhase().then(async phase => phase === 'connected' && state.dataCalls>limitedDataCalls), 'failed reads replayed after Retry-After');
    assert.equal(await bannerPhase(), null, 'banner disappears after recovery');
    assert.match(await cdp.evaluate("[...document.querySelectorAll('.mac-card')].find(card=>card.innerText.includes('知识库概览')).innerText"),/1 张表/,'the replayed response updates the original view');

    // A 500 is recoverable and the manual Retry repeats the actual failed read.
    state.mode = 'error';
    await cdp.evaluate("[...document.querySelectorAll('.dashboard-actions button')].find(button=>button.innerText.trim()==='刷新')?.click()");
    await waitFor(() => bannerPhase().then(phase => phase === 'retrying'), 'retryable 500 banner');
    assert.equal(await workspaceVisible(), true, 'workspace kept during upstream failure');
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('[data-testid=session-reauthorize]'))"), false, '5xx never asks to reauthorize');
    const errorDataCalls=state.dataCalls;
    state.mode = 'ok';
    await cdp.evaluate("document.querySelector('[data-testid=session-retry]')?.click()");
    await waitFor(() => cdp.evaluate("document.querySelector('[data-testid=session-status-banner]')===null") .then(visible => visible && state.dataCalls>errorDataCalls), 'failed reads replayed after manual retry');

    // A non-JSON gateway 502 follows the same recoverable path and can replay.
    state.mode = 'gateway';
    await cdp.evaluate("[...document.querySelectorAll('.dashboard-actions button')].find(button=>button.innerText.trim()==='刷新')?.click()");
    await waitFor(() => bannerPhase().then(phase => phase === 'retrying'), 'retryable non-JSON gateway response');
    assert.equal(await workspaceVisible(),true);
    const gatewayDataCalls=state.dataCalls;
    state.mode='ok';
    await cdp.evaluate("document.querySelector('[data-testid=session-retry]')?.click()");
    await waitFor(() => cdp.evaluate("document.querySelector('[data-testid=session-status-banner]')===null").then(visible=>visible&&state.dataCalls>gatewayDataCalls),'gateway request replayed');

    // Network fetch failures share the same classification and replay behavior.
    state.mode='network';
    await cdp.evaluate("[...document.querySelectorAll('.dashboard-actions button')].find(button=>button.innerText.trim()==='刷新')?.click()");
    await waitFor(() => bannerPhase().then(phase => phase === 'retrying'), 'retryable network failure');
    assert.equal(await workspaceVisible(),true);
    const networkDataCalls=state.dataCalls;
    state.mode='ok';
    await cdp.evaluate("document.querySelector('[data-testid=session-retry]')?.click()");
    await waitFor(() => cdp.evaluate("document.querySelector('[data-testid=session-status-banner]')===null").then(visible=>visible&&state.dataCalls>networkDataCalls),'network request replayed');

    // Revocation is the one case that legitimately requires reauthorization.
    state.mode = 'revoked';
    await cdp.evaluate("[...document.querySelectorAll('.dashboard-actions button')].find(button=>button.innerText.trim()==='刷新')?.click()");
    await waitFor(() => bannerPhase().then(phase => phase === 'reauthorize'), 'reauthorization banner after revocation');
    assert.equal(await workspaceVisible(), true, 'workspace stays until the user reauthorizes');
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('[data-testid=session-reauthorize]'))"), true);

    // A late refresh response cannot restore session state after explicit logout.
    state.mode='error';
    await cdp.evaluate("[...document.querySelectorAll('.dashboard-actions button')].find(button=>button.innerText.trim()==='刷新')?.click()");
    await waitFor(() => bannerPhase().then(phase=>phase==='retrying'),'retryable request before logout race');
    state.delayToken=true;
    await cdp.evaluate("document.querySelector('[data-testid=session-retry]')?.click()");
    await waitFor(()=>state.delayedTokenRequestId!==null,'delayed session refresh');
    await cdp.evaluate("document.querySelector('.menubar-app-menu > button').click()");
    await waitFor(()=>cdp.evaluate("Boolean([...document.querySelectorAll('[role=menuitem]')].find(item=>item.innerText==='退出登录'))"),'logout menu item');
    await cdp.evaluate("[...document.querySelectorAll('[role=menuitem]')].find(item=>item.innerText==='退出登录').click()");
    await waitFor(()=>cdp.evaluate("document.body.innerText.includes('登录 XeonoLab')"),'explicit logout');
    await fulfil(state.delayedTokenRequestId,200,{token:'late-access-token',expires:300});
    await sleep(100);
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('nav.dock'))"),false,'late token refresh does not restore the workspace');
    assert.equal(await statusPhase(),'signed-out');

    // A raw attachment GET already in flight when the user logs out is fenced too.
    state.mode='ok';
    state.delayToken=false;
    await cdp.evaluate('location.reload()');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('nav.dock'))"), 'desktop after synthetic session restore');
    await cdp.evaluate("[...document.querySelectorAll('nav.dock button')].find(button=>button.title==='知识库')?.click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-welcome-list button'))"), 'library restored for raw logout race');
    await cdp.evaluate("document.querySelector('.library-welcome-list button').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('tr[data-row-id=raw-row]'))"), 'raw logout race row');
    await cdp.evaluate("document.querySelector('tr[data-row-id=raw-row]').click()");
    await waitFor(() => cdp.evaluate("Boolean([...document.querySelectorAll('.attachment-panel button')].find(button=>button.innerText.trim()==='鉴权下载'))"), 'raw logout race download action');
    state.delayRaw=true;
    await cdp.evaluate("[...document.querySelectorAll('.attachment-panel button')].find(button=>button.innerText.trim()==='鉴权下载').click()");
    await waitFor(() => state.delayedRawRequestId!==null, 'delayed raw attachment response');
    const delayedRawRequestId=state.delayedRawRequestId;
    await cdp.evaluate("document.querySelector('.menubar-app-menu > button').click()");
    await waitFor(()=>cdp.evaluate("Boolean([...document.querySelectorAll('[role=menuitem]')].find(item=>item.innerText==='退出登录'))"),'logout menu item for raw request');
    await cdp.evaluate("[...document.querySelectorAll('[role=menuitem]')].find(item=>item.innerText==='退出登录').click()");
    await waitFor(()=>cdp.evaluate("document.body.innerText.includes('登录 XeonoLab')"),'logout while raw request is pending');
    await fulfilRaw(delayedRawRequestId,200,'late-raw-response','application/octet-stream');
    await sleep(100);
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('nav.dock'))"),false,'late raw response cannot restore the workspace after logout');
    assert.equal(await statusPhase(),'signed-out');

    const shotDir = process.env.UI_SCREENSHOT_DIR;
    if (shotDir) {
      await fs.mkdir(shotDir, {recursive: true});
      const shot = await cdp.call('Page.captureScreenshot', {format: 'png'});
      await fs.writeFile(path.join(shotDir, 'desktop-session-retry-dark.png'), Buffer.from(shot.data, 'base64'));
    }

    assert.ok(state.dataCalls >= 14, `fixture exercised data endpoints and retries (${state.dataCalls})`);
  } finally {
    cdp?.socket.close();
    try {
      await stopProcess(chrome);
    } finally {
      try {
        await server.close();
      } finally {
        await fs.rm(tempDir, {recursive: true, force: true}).catch(() => undefined);
      }
    }
  }
});
