#!/usr/bin/env node
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import net from 'node:net';
import {isExpectedUnregisteredAccountTreeResponse, mayInitializeAccountTree} from './account_tree_acceptance.mjs';

const timeoutMs = 20_000;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const safeText = value => String(value).replace(/[\u0000-\u001f]/g, ' ').slice(0, 500);
let activeReport;
let activeOutDir;
let activeSecrets = [];

function redactError(value) {
  return activeSecrets.reduce((text, secret) => text.replaceAll(secret, '[redacted]'), safeText(value));
}

function args() {
  const result = {};
  for (let i = 2; i < process.argv.length; i += 1) {
    if (process.argv[i] === '--config') result.config = process.argv[++i];
    else if (process.argv[i] === '--out') result.out = process.argv[++i];
    else if (process.argv[i] === '--chrome') result.chrome = process.argv[++i];
    else if (process.argv[i] === '--certutil') result.certutil = process.argv[++i];
    else if (process.argv[i] === '--ca-nickname') result.caNickname = process.argv[++i];
    else if (process.argv[i] === '--session-resilience') result.sessionResilience = true;
    else throw new Error(`Unknown argument ${process.argv[i]}`);
  }
  if (!result.config || !result.out) throw new Error('Usage: node scripts/browser_acceptance.mjs --config PRIVATE.json --out DIR [--chrome /path/to/chrome] [--session-resilience]');
  return result;
}

async function privateConfig(file, caNicknameOverride) {
  const stat = await fs.stat(file);
  if ((stat.mode & 0o077) !== 0) throw new Error('Credential file permissions must be 0600 (no group/other access).');
  const config = JSON.parse(await fs.readFile(file, 'utf8'));
  if (!config.appUrl || !config.username || !config.password || !(caNicknameOverride || config.caNickname)) {
    throw new Error('Private config must contain appUrl, username, password, and caNickname.');
  }
  const url = new URL(config.appUrl);
  if (url.protocol !== 'https:') throw new Error('appUrl must use HTTPS so browser certificate validation is exercised.');
  return config;
}

function verifyChromeCaTrust(command, nickname) {
  const home = process.env.HOME || os.homedir();
  const nssdb = path.join(home, '.pki', 'nssdb');
  const result = spawnSync(command, ['-L', '-d', `sql:${nssdb}`], {encoding: 'utf8'});
  if (result.error || result.status !== 0) {
    throw new Error(`Could not inspect Chrome's Linux NSS trust database at ${nssdb}; provide a usable --certutil path and have the coordinator import the CA first.`);
  }
  const quoted = nickname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const line = result.stdout.split(/\r?\n/).find(value => new RegExp(`^${quoted}\\s+([A-Za-z,]+)\\s*$`).test(value));
  const trust = line?.match(/^.*\s+([A-Za-z,]+)\s*$/)?.[1];
  if (!trust || !trust.split(',')[0].includes('C')) {
    throw new Error(`Chrome NSS database does not show CA nickname "${nickname}" with SSL CA trust; TLS validation remains enabled.`);
  }
}

async function reservePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', resolve).once('error', reject));
  const {port} = server.address();
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function waitFor(fn, description, timeout = timeoutMs) {
  const until = Date.now() + timeout;
  let last;
  while (Date.now() < until) {
    try { last = await fn(); if (last) return last; } catch {}
    await sleep(200);
  }
  throw new Error(`Timed out waiting for ${description}${last ? `: ${safeText(last)}` : ''}`);
}

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.id = 0;
    this.pending = new Map();
    this.events = new Map();
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const waiter = this.pending.get(message.id);
        if (waiter) {
          this.pending.delete(message.id);
          if (message.error) waiter.reject(new Error(message.error.message));
          else waiter.resolve(message.result);
        }
        return;
      }
      for (const listener of this.events.get(message.method) || []) listener(message.params);
    });
  }
  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, {once: true});
      socket.addEventListener('error', () => reject(new Error('Chrome DevTools connection failed')), {once: true});
    });
    return new CDP(socket);
  }
  on(method, listener) {
    if (!this.events.has(method)) this.events.set(method, []);
    this.events.get(method).push(listener);
  }
  call(method, params = {}) {
    const id = ++this.id;
    this.socket.send(JSON.stringify({id, method, params}));
    return new Promise((resolve, reject) => this.pending.set(id, {resolve, reject}));
  }
  async evaluate(expression) {
    const result = await this.call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'Browser evaluation failed');
    return result.result?.value;
  }
  close() { this.socket.close(); }
}

async function main() {
  const options = args();
  activeOutDir = path.resolve(options.out);
  await fs.mkdir(activeOutDir, {recursive: true});
  activeReport = {state: 'running', checks: [], screenshots: [], consoleErrors: [], pageExceptions: [], httpErrors: [], injectedSessionResponses: [], expectedPreAuth401: [], expectedUnregisteredAccountTree: [], accountTreeFirstUse: null, overflow: []};
  const config = await privateConfig(options.config, options.caNickname);
  activeSecrets = [config.username, config.password].filter(Boolean);
  const chromePath = options.chrome || '/usr/bin/google-chrome';
  const certutilPath = options.certutil || 'certutil';
  const caNickname = options.caNickname || config.caNickname;
  await fs.access(chromePath);
  // Check, but never modify, the Linux NSS database Chrome reads from the current HOME.
  // Certificate-error bypass flags are intentionally absent.
  verifyChromeCaTrust(certutilPath, caNickname);

  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xelonolab-browser-'));
  const profile = path.join(tempDir, 'chrome-profile');
  const outDir = activeOutDir;
  await fs.mkdir(profile, {recursive: true});
  await fs.mkdir(outDir, {recursive: true});

  const port = await reservePort();
  const chrome = spawn(chromePath, [
    '--headless=new', '--no-first-run', '--no-default-browser-check', '--window-size=1440,1000',
    `--user-data-dir=${profile}`, `--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1',
    '--remote-allow-origins=*',
    'about:blank',
  ], {env: process.env, stdio: 'ignore'});
  let cdp;
  let authenticated = false;
  const rowLoads = new Map();
  const accountTreeRequests = new Map();
  const pendingAccountTree404 = new Map();
  const pendingIdentityResponses = new Map();
  const injectedNetworkIds = new Set();
  let injectedStatus = 0;
  let desktopIdentity;
  const bootstrapPosts = [];
  const report = activeReport;
  const redact = value => [config.username, config.password].filter(Boolean).reduce((text, secret) => text.replaceAll(secret, '[redacted]'), safeText(value));
  const check = (name, passed, detail = '') => report.checks.push({name, passed, ...(detail ? {detail: safeText(detail)} : {})});
  try {
    await waitFor(async () => {
      if (chrome.exitCode !== null) throw new Error(`Chrome exited with code ${chrome.exitCode}`);
      const result = await fetch(`http://127.0.0.1:${port}/json/version`).catch(() => null);
      return result?.ok;
    }, 'Chrome DevTools endpoint');
    const newTarget = await fetch(`http://127.0.0.1:${port}/json/new?about%3Ablank`, {method: 'PUT'});
    if (!newTarget.ok) throw new Error('Could not create isolated Chrome tab.');
    const target = await newTarget.json();
    cdp = await CDP.connect(target.webSocketDebuggerUrl);
    cdp.on('Runtime.consoleAPICalled', event => {
      if (event.type === 'error') report.consoleErrors.push(redact(event.args.map(arg => arg.value || arg.description || '').join(' ')));
    });
    cdp.on('Runtime.exceptionThrown', event => report.pageExceptions.push(redact(event.exceptionDetails?.text || 'Uncaught page exception')));
    cdp.on('Network.requestWillBeSent', event => {
      const url = new URL(event.request.url);
      if (url.pathname === '/api/v1/auth/accounts') accountTreeRequests.set(event.requestId, event.request.method);
      if (/\/api\/v1\/tables\/[^/]+\/rows$/.test(url.pathname)) {
        rowLoads.set(event.requestId, {path: url.pathname, done: false, status: null});
      }
      if (url.pathname === '/api/v1/auth/accounts/bootstrap' && event.request.method === 'POST') {
        bootstrapPosts.push({method: event.request.method, url: url.pathname});
      }
    });
    cdp.on('Network.responseReceived', event => {
      const url = new URL(event.response.url);
      const rowLoad = rowLoads.get(event.requestId);
      if (rowLoad) rowLoad.status = event.response.status;
      if (url.pathname === '/api/v1/auth/accounts' && event.response.status === 404 && accountTreeRequests.get(event.requestId) === 'GET') {
        pendingAccountTree404.set(event.requestId, {method: 'GET', status: event.response.status, url: url.pathname});
        return;
      }
      if (url.pathname === '/api/v1/users/me' && event.response.status < 400) {
        pendingIdentityResponses.set(event.requestId, url.pathname);
      }
      if (event.response.status === 401 && url.pathname === '/api/v1/auth/session/token' && !authenticated) {
        report.expectedPreAuth401.push({status: 401, url: url.pathname});
        return;
      }
      if (event.response.status >= 400 && !event.response.url.startsWith('chrome-extension://') && !injectedNetworkIds.has(event.requestId)) {
        report.httpErrors.push({status: event.response.status, url: safeText(url.pathname)});
      }
    });
    cdp.on('Fetch.requestPaused', async event => {
      const pathName = new URL(event.request.url).pathname;
      if (injectedStatus && pathName === '/api/v1/auth/session/token') {
        if (event.networkId) injectedNetworkIds.add(event.networkId);
        report.injectedSessionResponses.push({status:injectedStatus,url:pathName});
        await cdp.call('Fetch.fulfillRequest', {requestId:event.requestId,responseCode:injectedStatus,headers:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify({error:{code:injectedStatus===401?'DESKTOP_ACCESS_REVOKED':'AUTH_UNAVAILABLE',message:'acceptance injected response'}})).toString('base64')});
      } else await cdp.call('Fetch.continueRequest', {requestId:event.requestId});
    });
    cdp.on('Network.loadingFinished', event => {
      const rowLoad = rowLoads.get(event.requestId);
      if (rowLoad) rowLoad.done = true;
      const accountTree404 = pendingAccountTree404.get(event.requestId);
      if (accountTree404) {
        pendingAccountTree404.delete(event.requestId);
        void cdp.call('Network.getResponseBody', {requestId: event.requestId}).then(response => {
          const raw = response.base64Encoded ? Buffer.from(response.body, 'base64').toString('utf8') : response.body;
          const payload = JSON.parse(raw);
          if (isExpectedUnregisteredAccountTreeResponse(accountTree404.method, accountTree404.url, accountTree404.status, payload)) {
            report.expectedUnregisteredAccountTree.push({method: accountTree404.method, status: accountTree404.status, code: payload.error.code, url: accountTree404.url});
          } else {
            report.httpErrors.push({status: accountTree404.status, url: accountTree404.url, code: safeText(payload?.error?.code || 'unrecognized account-tree response')});
          }
        }).catch(() => report.httpErrors.push({status: accountTree404.status, url: accountTree404.url, code: 'could not inspect response body'}));
      }
      if (pendingIdentityResponses.has(event.requestId)) {
        pendingIdentityResponses.delete(event.requestId);
        void cdp.call('Network.getResponseBody', {requestId: event.requestId}).then(response => {
          const raw = response.base64Encoded ? Buffer.from(response.body, 'base64').toString('utf8') : response.body;
          const payload = JSON.parse(raw);
          if (payload?.data?.kind && Array.isArray(payload.data.scopes)) desktopIdentity = payload.data;
        }).catch(() => {});
      }
    });
    cdp.on('Network.loadingFailed', event => {
      const rowLoad = rowLoads.get(event.requestId);
      if (rowLoad) {rowLoad.done = true; rowLoad.error = safeText(event.errorText);}
      const accountTree404 = pendingAccountTree404.get(event.requestId);
      if (accountTree404) {
        pendingAccountTree404.delete(event.requestId);
        report.httpErrors.push({status: accountTree404.status, url: accountTree404.url, code: 'response body unavailable'});
      }
      pendingIdentityResponses.delete(event.requestId);
    });
    await cdp.call('Page.enable');
    await cdp.call('Runtime.enable');
    await cdp.call('Network.enable');
    const screenshot = async filename => {
      const result = await cdp.call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
      await fs.writeFile(path.join(outDir, filename), Buffer.from(result.data, 'base64'), {mode: 0o600});
      report.screenshots.push(filename);
    };
    const pageUrl = async () => cdp.evaluate('location.href');
    const clickButton = async label => cdp.evaluate(`(()=>{const e=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()===${JSON.stringify(label)});if(!e)return false;e.click();return true})()`);
    const clickTab = async label => cdp.evaluate(`(()=>{const e=[...document.querySelectorAll('.settings-tabs button')].find(x=>x.innerText.trim()===${JSON.stringify(label)});if(!e)return false;e.click();return true})()`);
    const clickVisible = async selectors => cdp.evaluate(`(()=>{for(const selector of ${JSON.stringify(selectors)}){let e;try{e=document.querySelector(selector)}catch{};if(e&&e.getClientRects().length){e.click();return true}}return false})()`);
    const measureOverflow = async label => {
      const metrics = await cdp.evaluate(`(()=>{const selectors=['html','body','.window-body','.knowledge','.knowledge-sidebar','.knowledge-content','.acl-manager','.attachment-panel'];return selectors.flatMap(s=>[...document.querySelectorAll(s)].map((e,i)=>({selector:s,index:i,clientWidth:e.clientWidth,scrollWidth:e.scrollWidth,clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,overflowX:e.scrollWidth>e.clientWidth+2,overflowY:e.scrollHeight>e.clientHeight+2})))})()`);
      report.overflow.push({page: label, metrics});
      const horizontal = metrics.filter(item => item.overflowX);
      check(`${label}: no horizontal overflow`, horizontal.length === 0, horizontal.map(item => item.selector).join(', '));
    };

    await cdp.call('Page.navigate', {url: config.appUrl});
    const loaded = await waitFor(async () => {
      const current = await pageUrl().catch(() => '');
      if (current.startsWith('chrome-error://')) return {failure: await cdp.evaluate('document.body.innerText').catch(() => 'Chrome TLS/navigation error')};
      return current.startsWith(config.appUrl) ? {ok: true} : null;
    }, 'desktop login page');
    if (loaded.failure) throw new Error(`Chrome rejected HTTPS navigation: ${safeText(loaded.failure)}`);
    await waitFor(() => cdp.evaluate("document.querySelector('button')?.innerText.includes('登录 XeonoLab')"), 'login control');
    await screenshot('01-login.png');
    if (!await clickButton('登录 XeonoLab')) throw new Error('Login button not found.');
    await waitFor(async () => (await pageUrl()).startsWith(new URL(config.appUrl).origin) ? false : (await pageUrl()).startsWith('https://'), 'identity provider redirect');
    const usernameSelector = config.usernameSelector || '#username';
    const passwordSelector = config.passwordSelector || '#password';
    const submitSelector = config.submitSelector || '#kc-login';
    await waitFor(() => cdp.evaluate(`Boolean(document.querySelector(${JSON.stringify(usernameSelector)})&&document.querySelector(${JSON.stringify(passwordSelector)}))`), 'identity username/password fields');
    // Capture before injecting credentials so no password or username appears in screenshots.
    await screenshot('02-identity-login.png');
    const fill = await cdp.evaluate(`(()=>{const u=document.querySelector(${JSON.stringify(usernameSelector)}),p=document.querySelector(${JSON.stringify(passwordSelector)});if(!u||!p)return false;u.value=${JSON.stringify(config.username)};p.value=${JSON.stringify(config.password)};u.dispatchEvent(new Event('input',{bubbles:true}));p.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
    if (!fill) throw new Error('Could not fill identity fields.');
    const submitted = await cdp.evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(submitSelector)});if(!e)return false;e.click();return true})()`);
    if (!submitted) throw new Error('Identity-provider submit control not found; configure submitSelector in the private config.');
    await waitFor(async () => (await pageUrl()).startsWith(new URL(config.appUrl).origin) && await cdp.evaluate("Boolean(document.querySelector('nav[aria-label=\"应用程序\"]'))"), 'authenticated desktop');
    authenticated = true;
    check('authenticated desktop and application dock', true);
    await measureOverflow('desktop');
    await screenshot('03-desktop.png');

    if (options.sessionResilience) {
      if (!await cdp.evaluate("(()=>{if(document.querySelector('.window'))return true;const b=document.querySelector('nav.dock button[title=\"系统面板\"]');if(!b)return false;b.click();return true})()")) throw new Error('Could not open a workspace window for session resilience checks.');
      await waitFor(()=>cdp.evaluate("Boolean(document.querySelector('.window'))"),'workspace window before session resilience checks');
      await cdp.call('Fetch.enable',{patterns:[{urlPattern:'*://*/api/v1/auth/session/token',requestStage:'Request'}]});
      injectedStatus=503;
      await cdp.evaluate("document.dispatchEvent(new Event('visibilitychange'))");
      await waitFor(()=>cdp.evaluate("document.querySelector('[data-testid=\"session-status\"]')?.getAttribute('data-phase') === 'retrying'"),'temporary session recovery status');
      const preserved503=await cdp.evaluate("Boolean(document.querySelector('nav.dock')&&document.querySelector('.window'))");
      check('temporary 503 preserves dock and open windows',preserved503,'Injected only at /api/v1/auth/session/token.');
      await screenshot('03a-session-503-preserved.png');
      injectedStatus=401;
      await cdp.evaluate("document.dispatchEvent(new Event('visibilitychange'))");
      await waitFor(()=>cdp.evaluate("document.querySelector('[data-testid=\"session-status\"]')?.getAttribute('data-phase') === 'reauthorize'"),'reauthorization state after session 401');
      const preserved401=await cdp.evaluate("Boolean(document.querySelector('nav.dock')&&document.querySelector('.window')&&document.querySelector('[data-testid=\"session-reauthorize\"]'))");
      check('session 401 keeps workspace and offers explicit reauthorization',preserved401,'Injected only at /api/v1/auth/session/token.');
      await screenshot('03b-session-401-preserved.png');
      injectedStatus=0;
      await cdp.call('Fetch.disable');
      await cdp.evaluate("document.dispatchEvent(new Event('visibilitychange'))");
      const resumed=await waitFor(()=>cdp.evaluate("document.querySelector('[data-testid=\"session-status\"]')?.getAttribute('data-phase') === 'connected'"),'session recovery after removing injected responses').catch(()=>false);
      check('normal session endpoint resumes after injection is removed',Boolean(resumed),'Intercept is removed and regular response handling remains enabled.');
    }

    if (!await cdp.evaluate("(()=>{const e=document.querySelector('button[title=\"LaunchPad\"]');if(!e)return false;e.click();return true})()")) throw new Error('LaunchPad control not found.');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.launchpad'))"), 'LaunchPad');
    await screenshot('04-launchpad.png');
    if (!await cdp.evaluate("(()=>{const e=[...document.querySelectorAll('.launchpad button')].find(x=>x.innerText.includes('知识库'));if(!e)return false;e.click();return true})()")) throw new Error('Knowledge app is not present in LaunchPad for this account.');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.knowledge'))"), 'Knowledge page');
    await measureOverflow('knowledge');
    await screenshot('05-knowledge.png');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.knowledge-sidebar button:nth-of-type(2)')&&!document.querySelector('.knowledge-sidebar button:nth-of-type(2)').disabled)"), 'knowledge table list loading');
    const tableAvailable = await cdp.evaluate("Boolean(document.querySelector('.knowledge-sidebar button strong'))");
    if (tableAvailable) {
      const previousRowRequestIds = new Set(rowLoads.keys());
      await cdp.evaluate("document.querySelector('.knowledge-sidebar button:has(strong)')?.click()");
      const rowLoad = await waitFor(() => [...rowLoads].find(([id, request]) => !previousRowRequestIds.has(id) && request.done)?.[1] || null, 'knowledge rows API response');
      const rowsRequestOk = rowLoad.status >= 200 && rowLoad.status < 300;
      check('knowledge row list request succeeded', rowsRequestOk, rowLoad.status ? `HTTP ${rowLoad.status}` : rowLoad.error || 'No successful response status');
      await cdp.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>setTimeout(resolve,100))))');
      const rowState = await cdp.evaluate("(()=>({available:Boolean(document.querySelector('.table-scroll tbody tr')),empty:[...document.querySelectorAll('.table-scroll p')].some(p=>p.innerText.trim()==='没有可显示的记录。')}))()");
      check('knowledge row loading rendered a row or explicit empty state', rowState.available || rowState.empty, `rows available=${rowState.available}; empty state=${rowState.empty}`);
      const rowAvailable = rowsRequestOk && rowState.available;
      if (rowAvailable) {
        await cdp.evaluate("document.querySelector('.table-scroll tbody tr')?.click()");
        await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.record-editor'))"), 'selected record');
        await measureOverflow('attachment panel');
        await screenshot('06-attachments.png');
        check('attachment panel visible for first readable row', await cdp.evaluate("Boolean(document.querySelector('.attachment-panel'))"));
        check('attachment fixture visible', await cdp.evaluate("Boolean(document.querySelector('.attachment-panel li'))"), 'Select/seed a readable row with an attachment to exercise the populated attachment view.');
      } else {
        check('attachment panel fixture row available', false, 'No readable row exists; no data was created or changed.');
      }
    } else {
      check('readable knowledge table available', false, 'No readable table is visible for this account.');
    }

    await cdp.evaluate("[...document.querySelectorAll('button[title]')].find(e=>e.title==='系统设置')?.click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.settings-panel'))"), 'Settings page');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.account-setup'))"), 'first-use account-tree setup state');
    await waitFor(() => report.expectedUnregisteredAccountTree.length > 0 || report.httpErrors.some(item => item.url === '/api/v1/auth/accounts'), 'account-tree response classification');
    const firstUseUI = await cdp.evaluate(`(()=>{const setup=document.querySelector('.account-setup');const text=setup?.innerText||'';const button=[...(setup?.querySelectorAll('button')||[])].find(e=>e.innerText.trim()==='初始化我的账号树');return {visible:Boolean(setup&&setup.getClientRects().length),heading:setup?.querySelector('h3')?.innerText.trim()||'',guidance:text.includes('当前登录身份尚未登记到受管账号树')&&text.includes('不会自动启用账号树或增加权限'),initializeButtonVisible:Boolean(button&&button.getClientRects().length),contactPromptVisible:text.includes('请联系具有桌面管理员权限的人工用户完成初始化'),raw404ErrorVisible:document.body.innerText.includes('account not registered')}})()`);
    const accountMayInitialize = mayInitializeAccountTree(desktopIdentity);
    const recordedAccountTreeResponse = report.expectedUnregisteredAccountTree.at(-1) || null;
    report.accountTreeFirstUse = {
      response: recordedAccountTreeResponse,
      setupVisible: firstUseUI.visible,
      guidanceVisible: firstUseUI.guidance,
      identityMayInitialize: accountMayInitialize,
      initializeButtonVisible: firstUseUI.initializeButtonVisible,
      contactPromptVisible: firstUseUI.contactPromptVisible,
      bootstrapPostCount: bootstrapPosts.length,
    };
    check('unregistered account-tree response recorded as expected', Boolean(recordedAccountTreeResponse), recordedAccountTreeResponse ? `${recordedAccountTreeResponse.status} ${recordedAccountTreeResponse.code}` : 'Expected GET /api/v1/auth/accounts to return 404 ACCOUNT_NOT_FOUND.');
    check('first-use setup explanation visible without raw 404 error', firstUseUI.visible && firstUseUI.heading === '首次启用账号树' && firstUseUI.guidance && !firstUseUI.raw404ErrorVisible, firstUseUI.heading || 'Setup heading missing.');
    check('initialization entry matches authenticated identity permissions', Boolean(desktopIdentity) && (accountMayInitialize ? firstUseUI.initializeButtonVisible && !firstUseUI.contactPromptVisible : !firstUseUI.initializeButtonVisible && firstUseUI.contactPromptVisible), desktopIdentity ? `eligible=${accountMayInitialize}` : 'Could not inspect /api/v1/users/me response.');
    check('first-use browser check does not bootstrap or mutate the account tree', bootstrapPosts.length === 0, `${bootstrapPosts.length} bootstrap request(s) observed.`);
    await measureOverflow('account-tree first-use setup');
    await screenshot('06a-settings-account-tree-first-use.png');
    await clickTab('知识库 ACL');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.acl-manager'))"), 'ACL settings tab');
    await measureOverflow('ACL settings');
    await screenshot('07-settings-acl.png');
    check('ACL settings tab visible', true);
    await clickTab('身份与权限');
    await waitFor(() => cdp.evaluate("[...document.querySelectorAll('.settings-panel h2')].some(e=>e.innerText.trim()==='身份与权限')"), 'identity and permissions settings');
    await measureOverflow('identity and permissions');
    await screenshot('08-settings-permissions.png');
    check('identity and permissions tab visible', true);

    const featureSelectors = config.issue41 || {};
    await cdp.evaluate("[...document.querySelectorAll('.window')].find(e=>e.querySelector('.settings-panel'))?.querySelector('.bar .close')?.click()");
    await waitFor(() => cdp.evaluate("!document.querySelector('.settings-panel')"), 'close Settings window');
    const clockSelector = featureSelectors.widgetClock || '.widgets .widget-clock';
    const statusSelector = featureSelectors.widgetStatus || '.widgets .widget-status';
    const widgetState = await cdp.evaluate(`(()=>{const visible=s=>{const e=document.querySelector(s);return Boolean(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden')};return {clock:visible(${JSON.stringify(clockSelector)}),status:visible(${JSON.stringify(statusSelector)})}})()`);
    check('desktop clock/date widget visible', widgetState.clock, `selector: ${clockSelector}`);
    check('desktop system-status widget visible', widgetState.status, `selector: ${statusSelector}`);
    await measureOverflow('desktop widgets');
    await screenshot('09-desktop-widgets.png');

    const systemToggle = featureSelectors.systemPanelToggle || 'button[title="打开系统面板"]';
    const clickedSystemPanel = await clickVisible([systemToggle]);
    check('system-status widget opens panel', clickedSystemPanel, `selector: ${systemToggle}`);
    const panelSelector = featureSelectors.systemPanel || '.window .dashboard';
    const panelVisible = await waitFor(() => cdp.evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(panelSelector)});return Boolean(e&&e.getClientRects().length)})()`), 'system panel dashboard visible').catch(() => false);
    check('system panel opened', panelVisible, `selector: ${panelSelector}`);
    if (panelVisible) {
      const panelTitle = await cdp.evaluate(`document.querySelector(${JSON.stringify(panelSelector)})?.querySelector('h2')?.innerText.trim()||''`);
      check('system panel is titled 系统面板', panelTitle === '系统面板', panelTitle);
      await measureOverflow('system panel dashboard');
      await screenshot('10-system-panel-dashboard.png');
          const dashboardDetails = await cdp.evaluate(`(()=>{const d=document.querySelector(${JSON.stringify(panelSelector)});if(!d)return {ring:false,roundedRing:false,bars:false,roundedBars:false,meter:false,roundedMeter:false,indexQueue:false,workQueue:false};const visible=e=>e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';const cardWith=label=>[...d.querySelectorAll('.mac-card')].find(card=>card.querySelector('h3')?.innerText.includes(label));const embedding=cardWith('我的 Embedding 索引'),work=cardWith('Harness 任务队列');const ring=d.querySelector('.mac-ring'),ringSegments=[...(ring?.querySelectorAll('.ring-segment')||[])].filter(visible);const meter=d.querySelector('.mac-meter'),meterStyle=meter?getComputedStyle(meter):null;const bars=d.querySelector('.mac-bars'),plots=[...(bars?.querySelectorAll('.mac-plot')||[])].filter(visible),plotStyle=plots[0]?getComputedStyle(plots[0]):null;const corners=style=>style&&parseFloat(style.borderTopLeftRadius)>0&&style.overflowX==='hidden'&&style.overflowY==='hidden';return {ring:Boolean(visible(ring)),roundedRing:ringSegments.some(circle=>circle.getAttribute('stroke-linecap')==='round'||getComputedStyle(circle).strokeLinecap==='round'),bars:Boolean(visible(bars)),roundedBars:plots.length>0&&corners(plotStyle),meter:Boolean(visible(meter)),roundedMeter:Boolean(meterStyle&&parseFloat(meterStyle.borderTopLeftRadius)>0&&meterStyle.overflowX==='hidden'&&meterStyle.overflowY==='hidden'),indexQueue:Boolean(embedding&&visible(embedding)&&visible(embedding.querySelector('.mac-meter[role="img"]'))),workQueue:Boolean(work&&visible(work)&&visible(work.querySelector('.mac-ring[role="img"]')))}})()`);
      check('Harness activity ring visible', dashboardDetails.ring);
      check('Harness SVG ring uses rounded stroke-linecap', dashboardDetails.roundedRing, 'requires a visible .ring-segment with stroke-linecap="round"');
      check('mac-bars chart visible', dashboardDetails.bars, 'requires table data for the knowledge overview chart');
      check('mac-bars plot has rounded clipping', dashboardDetails.roundedBars, 'requires a visible .mac-plot with border-radius and overflow hidden');
      check('mac-meter visible', dashboardDetails.meter);
      check('mac-meter has rounded clipping', dashboardDetails.roundedMeter, 'requires border-radius and overflow hidden on .mac-meter');
      check('Embedding queue card and meter visible', dashboardDetails.indexQueue);
      check('Harness queue card and ring visible', dashboardDetails.workQueue);
    }

    await cdp.evaluate("document.querySelector('button[title=\"LaunchPad\"]')?.click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.launchpad'))"), 'LaunchPad for search acceptance');
    const searchSelector = featureSelectors.launchpadSearch || '.launchpad-search';
    const searchExists = await cdp.evaluate(`Boolean(document.querySelector(${JSON.stringify(searchSelector)})?.getClientRects().length)`);
    check('LaunchPad search input found', searchExists, `selector: ${searchSelector}`);
    if (searchExists) {
      await cdp.evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(searchSelector)});if(!e)return false;Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'系统面板');e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
      await measureOverflow('LaunchPad search');
      await screenshot('11-launchpad-search-system-panel.png');
      const dashboardHit = await cdp.evaluate("(()=>[...document.querySelectorAll('.launchpad button')].some(e=>e.getClientRects().length&&e.innerText.includes('系统面板')))()");
      check('LaunchPad search shows 系统面板 result', dashboardHit, 'Search uses the Chinese app title; expected one visible “系统面板” tile.');
      if (dashboardHit) {
        const clickedDashboard = await cdp.evaluate("(()=>{const e=[...document.querySelectorAll('.launchpad button')].find(x=>x.getClientRects().length&&x.innerText.includes('系统面板'));if(!e)return false;e.click();return true})()");
        check('LaunchPad 系统面板 result opened', clickedDashboard);
        if (clickedDashboard) {
          const dashboardVisible = await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window .dashboard'))"), 'Dashboard opened from LaunchPad search').catch(() => false);
          check('Dashboard remains open after LaunchPad result click', dashboardVisible);
          await measureOverflow('Dashboard after LaunchPad search');
          await screenshot('12-dashboard-from-search.png');
        }
      }
    }

    check('browser console errors absent', report.consoleErrors.length === 0, `${report.consoleErrors.length} console error(s)`);
    check('uncaught page exceptions absent', report.pageExceptions.length === 0, `${report.pageExceptions.length} exception(s)`);
    check('unexpected HTTP error responses absent', report.httpErrors.length === 0, `${report.httpErrors.length} HTTP error response(s)`);
    report.state = report.checks.some(item => !item.passed) ? 'failed_checks' : 'completed';
    await fs.writeFile(path.join(outDir, 'report.json'), `${JSON.stringify(report, null, 2)}\n`, {mode: 0o600});
    process.stdout.write(JSON.stringify({state: report.state, screenshots: report.screenshots, checks: report.checks, report: path.join(outDir, 'report.json')}, null, 2) + '\n');
    if (report.checks.some(item => !item.passed)) process.exitCode = 1;
  } finally {
    cdp?.close();
    chrome.kill('SIGTERM');
    await sleep(250);
    if (chrome.exitCode === null) chrome.kill('SIGKILL');
    await fs.rm(tempDir, {recursive: true, force: true});
  }
}

main().catch(async error => {
  // Keep arbitrary config and credential contents out of logs.
  const message = redactError(error.message);
  if (activeReport && activeOutDir) {
    activeReport.state = 'error';
    activeReport.error = message;
    await fs.writeFile(path.join(activeOutDir, 'report.json'), `${JSON.stringify(activeReport, null, 2)}\n`, {mode: 0o600}).catch(() => {});
  }
  process.stderr.write(`Browser acceptance failed: ${message}${activeOutDir ? `; partial report: ${path.join(activeOutDir, 'report.json')}` : ''}\n`);
  process.exitCode = 1;
});
