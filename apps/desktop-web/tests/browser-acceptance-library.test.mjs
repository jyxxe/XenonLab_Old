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

async function removeTree(directory) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      await fs.rm(directory, {recursive: true, force: true});
      return;
    } catch (error) {
      if (!['ENOTEMPTY', 'EBUSY'].includes(error.code) || attempt >= 5) throw error;
      await sleep(100 * (attempt + 1));
    }
  }
}

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (!message.id) {
        this.events.push(message);
        return;
      }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
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

const setValue = `(selector, value) => {const element=document.querySelector(selector);if(!element)return false;const proto=element.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:element.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(element,value);element.dispatchEvent(new Event('input',{bubbles:true}));element.dispatchEvent(new Event('change',{bubbles:true}));return true}`;

test('XeLibrary application acceptance: navigation, table, inspector, search, query, theorems', async t => {
  const chromePath = process.env.CHROME_BIN || ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find(file => existsSync(file));
  if (!chromePath) {
    t.skip('Chrome/Chromium unavailable; run with CHROME_BIN=/path/to/chrome');
    return;
  }
  const portNumber = await port();
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: portNumber, strictPort: true, watch: null}});
  await server.listen();
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-library-acceptance-'));
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
    await cdp.call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
    await cdp.call('Page.navigate', {url: `http://127.0.0.1:${portNumber}/tests/library-fixture.html`});

    const shotDir = process.env.UI_SCREENSHOT_DIR;
    if (shotDir) await fs.mkdir(shotDir, {recursive: true});
    const capture = async name => {
      if (!shotDir) return;
      const result = await cdp.call('Page.captureScreenshot', {format: 'png'});
      await fs.writeFile(path.join(shotDir, `${name}.png`), Buffer.from(result.data, 'base64'));
    };

    // Sidebar navigation and catalogue rendering.
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.knowledge .knowledge-sidebar'))"), 'library sidebar');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.knowledge-sidebar button strong'))"), 'table in path tree');
    const sidebarState = await cdp.evaluate(`(() => ({
      tables: [...document.querySelectorAll('.knowledge-sidebar .library-tree-table')].map(button=>button.innerText.replace(/\\n/g,' ')),
      nodes: [...document.querySelectorAll('.knowledge-sidebar .library-tree-node')].length,
    }))()`);
    assert.ok(sidebarState.tables.some(item => item.includes('uniform-convergence')), 'table appears in the path tree');
    assert.ok(sidebarState.nodes >= 2, 'path nodes render');

    // Open the table; verify cursor pagination + virtualised rendering.
    await cdp.evaluate("[...document.querySelectorAll('.knowledge-sidebar .library-tree-table')].find(button=>button.innerText.includes('uniform-convergence')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.table-scroll tbody tr'))"), 'table rows');
    const tableState = await cdp.evaluate(`(() => ({
      rendered: document.querySelectorAll('.table-scroll tbody tr').length,
      loaded: document.querySelector('.library-table-count')?.innerText || '',
      columns: [...document.querySelectorAll('.table-scroll thead th')].map(th=>th.innerText.trim()),
    }))()`);
    assert.ok(tableState.rendered >= 8, 'rows rendered');
    assert.ok(tableState.rendered <= 60, `virtualised rendering keeps the DOM small (${tableState.rendered})`);
    assert.match(tableState.loaded, /99 行已加载/, 'deleted rows stay hidden by default');
    assert.ok(tableState.columns.some(label => label.includes('title')), `schema-aware columns render: ${JSON.stringify(tableState.columns)}`);
    await capture('xelibrary-table-dark-1440x900');

    // Sort and filter stay client-side over loaded rows.
    await cdp.evaluate("[...document.querySelectorAll('.table-scroll thead .library-sort')].find(button=>button.innerText.includes('title')).click()");
    await sleep(100);
    const sortedFirst = await cdp.evaluate("document.querySelector('.table-scroll tbody tr td:nth-child(2)')?.innerText");
    assert.ok(sortedFirst, 'sorted first row has a title cell');

    // Select a row: inspector opens, attachment panel is visible.
    await cdp.evaluate("document.querySelector('.table-scroll tbody tr').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.record-editor'))"), 'record editor');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.attachment-panel li'))"), 'attachment list');
    await capture('xelibrary-inspector-dark-1440x900');
    await cdp.evaluate("[...document.querySelectorAll('.inspector-tabs .ds-tab')].find(tab=>tab.innerText.includes('索引')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.inspector-index')?.innerText.includes('job-9')"), 'table-scoped index job');
    const indexPanel = await cdp.evaluate("document.querySelector('.inspector-index')?.innerText || ''");
    assert.ok(indexPanel.includes('已索引行'), 'per-table index status API feeds the panel');
    assert.ok(/100/.test(indexPanel) && /137/.test(indexPanel), `coverage counts render: ${JSON.stringify(indexPanel)}`);
    await capture('issue-72-index-status-1440x900');

    // A rejected index mutation must preserve correlation metadata in its toast.
    await cdp.evaluate("window.libraryFixture.state.reindexFailure='correlated'; [...document.querySelectorAll('.inspector-index button')].find(button=>button.innerText.includes('重建 Embedding 索引')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.library-toast-danger .ds-request-id')?.innerText==='请求编号 req-reindex-failed'"), 'reindex failure correlation');
    assert.equal(await cdp.evaluate("document.querySelector('.library-toast-danger .ds-error-code').innerText"), '错误代码 EMBEDDING_UNAVAILABLE');
    await capture('xelibrary-reindex-error-dark-1440x900');
    assert.ok(!(await cdp.evaluate("document.querySelector('.library-toast-danger').innerText")).includes('已排队'), 'failed reindex never reports a queued task');
    assert.equal((await cdp.evaluate("window.libraryFixture.calls.filter(call=>call.path==='/index/reindex').at(-1)")).body.table_id, 't1', 'failure came from the selected table index request');

    await cdp.evaluate("window.libraryFixture.state.reindexFailure='missing'; [...document.querySelectorAll('.inspector-index button')].find(button=>button.innerText.includes('重建 Embedding 索引')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-toast-danger .ds-error-code')) && !document.querySelector('.library-toast-danger .ds-request-id')"), 'reindex missing ID is not invented');
    await cdp.evaluate("window.libraryFixture.state.reindexFailure=''; [...document.querySelectorAll('.inspector-index button')].find(button=>button.innerText.includes('重建 Embedding 索引')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.library-toast-success')?.innerText.includes('已排队')"), 'reindex recovery notice');
    assert.equal(await cdp.evaluate("document.querySelectorAll('.library-toast .ds-request-id,.library-toast .ds-error-code').length"), 0, 'success notice does not retain previous failure metadata');

    // Field editing saves through If-Match.
    await cdp.evaluate("[...document.querySelectorAll('.inspector-tabs .ds-tab')].find(tab=>tab.innerText.includes('字段')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.inspector-fields input'))"), 'field editor');
    await cdp.evaluate(`(${setValue})('.inspector-fields input', '一致收敛定义（已编辑）')`);
    await cdp.evaluate("[...document.querySelectorAll('.inspector-fields button')].find(button=>button.innerText.includes('保存记录')).click()");
    await waitFor(() => cdp.evaluate("window.libraryFixture.state.saved.length===1"), 'row save recorded');
    const savedRevision = await cdp.evaluate('window.libraryFixture.state.saved[0].revision');
    assert.equal(savedRevision, 3, 'save sends the loaded revision');

    // Optimistic concurrency: 412 surfaces a conflict and never overwrites.
    await cdp.evaluate('window.libraryFixture.state.conflict=true');
    await cdp.evaluate(`(${setValue})('.inspector-fields input', '冲突写入')`);
    await cdp.evaluate("[...document.querySelectorAll('.inspector-fields button')].find(button=>button.innerText.includes('保存记录')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.inspector-conflict'))"), 'conflict banner');
    const conflictText = await cdp.evaluate("document.querySelector('.inspector-conflict').innerText");
    assert.match(conflictText, /已被他人修改/);
    assert.equal(await cdp.evaluate('window.libraryFixture.state.saved.length'), 1, 'conflicting save did not commit');
    await cdp.evaluate('window.libraryFixture.state.conflict=false');
    await cdp.evaluate("[...document.querySelectorAll('.inspector-conflict button')].find(button=>button.innerText.includes('重新加载')).click()");
    await waitFor(() => cdp.evaluate("!document.querySelector('.inspector-conflict')"), 'conflict cleared after reload');

    // Unified search: Fast semantic results, completeness warnings, exact-row navigation.
    await cdp.evaluate("[...document.querySelectorAll('.library-nav button')].find(button=>button.innerText.includes('搜索')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-search'))"), 'search view');
    await cdp.evaluate("document.querySelector('.library-search-filters .library-link').click()");
    await cdp.evaluate(`(${setValue})('.library-search-filter-body textarea', 'type = theorem')`);
    await cdp.evaluate(`(${setValue})('.library-search-bar input', '一致收敛')`);
    await cdp.evaluate("[...document.querySelectorAll('.library-search-bar button')].find(button=>button.innerText.trim()==='搜索').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-results li'))"), 'search results');
    const filteredRequest = await cdp.evaluate("window.libraryFixture.calls.filter(call=>call.path==='/search/semantic').at(-1)");
    assert.equal(filteredRequest.body.filters.type, 'theorem', 'the latest field filter is included in the search request');
    const warningText = await cdp.evaluate("document.querySelector('.library-warning')?.innerText || ''");
    assert.match(warningText, /2,341|2341/, 'missing embeddings are surfaced');
    await capture('xelibrary-search-dark-1440x900');
    await cdp.evaluate("document.querySelector('.library-results li button').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.record-editor')) && Boolean(document.querySelector('.table-scroll tbody tr.is-active-row'))"), 'search result opens the exact row');
    const activeRow = await cdp.evaluate("document.querySelector('.table-scroll tbody tr.is-active-row')?.dataset.rowId");
    assert.equal(activeRow, 'row-001');

    // A delayed search-result lookup cannot inject a row into a different active table.
    await cdp.evaluate("[...document.querySelectorAll('.library-nav button')].find(button=>button.innerText.includes('搜索')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-results li button'))"), 'search results before navigation race');
    await cdp.evaluate("window.libraryFixture.state.slowRowLookup=true; document.querySelector('.library-results li button').click()");
    await waitFor(() => cdp.evaluate("window.libraryFixture.calls.some(call=>call.path==='/tables/t1/rows/row-001')"), 'delayed result lookup started');
    await cdp.evaluate("[...document.querySelectorAll('.knowledge-sidebar .library-tree-table')].find(button=>button.innerText.includes('notes')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.table-scroll tbody tr')?.dataset.rowId==='t2-row-1'"), 'different table opened during result lookup');
    await sleep(600);
    const rowsAfterRace = await cdp.evaluate("({rows:[...document.querySelectorAll('.table-scroll tbody tr')].map(row=>row.dataset.rowId), active:document.querySelector('.table-scroll tbody tr.is-active-row')?.dataset.rowId})");
    assert.deepEqual(rowsAfterRace.rows, ['t2-row-1'], 'the stale row is not appended to the newly selected table');
    assert.equal(rowsAfterRace.active, undefined, 'the stale lookup cannot select a row in the new table');
    await cdp.evaluate('window.libraryFixture.state.slowRowLookup=false');

    // Quality is unavailable and shown as such, not faked.
    await cdp.evaluate("[...document.querySelectorAll('.library-nav button')].find(button=>button.innerText.includes('搜索')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-search'))"), 'search view again');
    await cdp.evaluate("(()=>{const box=document.querySelector('.library-search-kind input');box.checked=true;box.dispatchEvent(new Event('change',{bubbles:true}));})()");
    await cdp.evaluate("[...document.querySelectorAll('.library-search .ds-tab')].find(tab=>tab.innerText.includes('Quality')).click()");
    await cdp.evaluate(`(${setValue})('.library-search-bar input', '任意查询')`);
    await cdp.evaluate("[...document.querySelectorAll('.library-search-bar button')].find(button=>button.innerText.trim()==='搜索').click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.ds-error')?.innerText.includes('Quality 搜索不可用')"), 'quality unavailable state');
    const qualityRequest = await cdp.evaluate("window.libraryFixture.calls.filter(call=>call.path==='/search/semantic').at(-1)");
    assert.equal(qualityRequest.body.mode, 'quality', 'switching from keyword Fast mode still calls the Quality backend');

    // Query console: valid run, syntax error, known unsupported features.
    await cdp.evaluate("[...document.querySelectorAll('.library-nav button')].find(button=>button.innerText.includes('查询控制台')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-query'))"), 'query console');
    await cdp.evaluate("[...document.querySelectorAll('.library-query-toolbar button')].find(button=>button.innerText.trim()==='运行').click()");
    await waitFor(() => cdp.evaluate("document.querySelectorAll('.library-query-results tbody tr').length===1"), 'query results');
    await capture('xelibrary-query-dark-1440x900');
    await cdp.evaluate(`(${setValue})('.library-query-editor', 'FROM "/**" BOGUS')`);
    const queryCountBeforeValidation = await cdp.evaluate("window.libraryFixture.calls.filter(call=>call.path==='/query').length");
    await cdp.evaluate("[...document.querySelectorAll('.library-query-toolbar button')].find(button=>button.innerText.includes('校验语法')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.library-query .ds-error')?.innerText.includes('查询语法错误')"), 'explicit query validation error');
    assert.equal(await cdp.evaluate("window.libraryFixture.calls.filter(call=>call.path==='/query').length"), queryCountBeforeValidation, 'validation never executes an invalid query');
    await cdp.evaluate("[...document.querySelectorAll('.library-query-toolbar button')].find(button=>button.innerText.trim()==='运行').click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.library-query .ds-error')?.innerText.includes('查询语法错误')"), 'query syntax error');
    await cdp.evaluate("[...document.querySelectorAll('.library-query-toolbar button')].find(button=>button.innerText.includes('已知未实现语法')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.library-query .library-notice')?.innerText.includes('EXISTS')"), 'known unsupported features');

    // Theorems: browse and show unavailable verifier capabilities honestly.
    await cdp.evaluate("[...document.querySelectorAll('.library-nav button')].find(button=>button.innerText.includes('定理')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-theorem-list li'))"), 'theorem list');
    await cdp.evaluate("window.libraryFixture.state.delayTheoremId='thm-1'; window.libraryFixture.state.denyTheoremRelations=true; [...document.querySelectorAll('.library-theorem-list li button')].find(button=>button.innerText.includes('一致收敛定理')).click()");
    await waitFor(() => cdp.evaluate("window.libraryFixture.calls.some(call=>call.path==='/theorems/thm-1')"), 'first theorem request started');
    await cdp.evaluate("[...document.querySelectorAll('.library-theorem-list li button')].find(button=>button.innerText.includes('极限唯一性定理')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-theorem-detail'))"), 'theorem detail');
    await waitFor(() => cdp.evaluate("document.querySelector('.library-theorem-detail .ds-error')?.innerText.includes('权限不足')"), 'theorem relationship permission error');
    assert.match(await cdp.evaluate("document.querySelector('.library-theorem-detail h3')?.innerText"), /极限唯一性定理/, 'stale detail responses do not replace the newly selected theorem');
    await cdp.evaluate("window.libraryFixture.state.denyTheoremRelations=false; window.libraryFixture.state.delayTheoremId=''; [...document.querySelectorAll('.library-theorem-list li button')].find(button=>button.innerText.includes('一致收敛定理')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.library-theorem-detail')?.innerText.includes('f_n 连续')"), 'complete theorem detail');
    const theoremDetail = await cdp.evaluate("document.querySelector('.library-theorem-detail').innerText");
    assert.match(theoremDetail, /Lean verification unavailable/);
    assert.match(theoremDetail, /Agent derivation unavailable/);
    assert.match(theoremDetail, /formal_status: none/, 'no fake verified status');

    // Switching tables never shows a previous table's index job during a slow refresh.
    await cdp.evaluate("[...document.querySelectorAll('.knowledge-sidebar .library-tree-table')].find(button=>button.innerText.includes('uniform-convergence')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.table-scroll tbody tr'))"), 'table opened before job switch');
    await cdp.evaluate("[...document.querySelectorAll('.inspector-tabs .ds-tab')].find(tab=>tab.innerText.includes('索引')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.inspector-index')?.innerText.includes('job-9')"), 'first table index job restored');
    await cdp.evaluate("window.libraryFixture.state.delayJobsTable='t2'; [...document.querySelectorAll('.knowledge-sidebar .library-tree-table')].find(button=>button.innerText.includes('notes')).click()");
    const jobsDuringSwitch = await cdp.evaluate("document.querySelector('.inspector-index')?.innerText || ''");
    assert.ok(!jobsDuringSwitch.includes('job-9'), 'the old table job is hidden immediately after switching tables');
    await sleep(650);
    const jobsAfterSwitch = await cdp.evaluate("({index:document.querySelector('.inspector-index')?.innerText || '', calls:window.libraryFixture.calls.filter(call=>call.path.startsWith('/jobs?'))})");
    assert.ok(jobsAfterSwitch.index.includes('job-t2'), `new table index job appears: ${JSON.stringify(jobsAfterSwitch)}`);
    await cdp.evaluate("window.libraryFixture.state.delayJobsTable=''; [...document.querySelectorAll('.knowledge-sidebar .library-tree-table')].find(button=>button.innerText.includes('uniform-convergence')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.table-scroll tbody tr'))"), 'original table restored');

    // Table operations: destructive schema warning, move, and explained delete failure.
    await cdp.evaluate("[...document.querySelectorAll('.knowledge-sidebar .library-tree-table')].find(button=>button.innerText.includes('uniform-convergence')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.table-scroll tbody tr'))"), 'table reopened');
    await cdp.evaluate("[...document.querySelectorAll('.library-table-actions button')].find(button=>button.innerText.includes('字段定义')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.ds-modal'))"), 'schema dialog');
    await cdp.evaluate("document.querySelector('.schema-editor .schema-field button:last-of-type').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.schema-risk-destructive'))"), 'destructive schema warning');
    await cdp.evaluate("[...document.querySelectorAll('.ds-modal-foot button')].find(button=>button.innerText.trim()==='取消').click()");

    await cdp.evaluate("[...document.querySelectorAll('.library-table-actions button')].find(button=>button.innerText.trim()==='移动').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.ds-modal'))"), 'move dialog');
    await cdp.evaluate(`(${setValue})('.ds-modal input', '/数学/复分析')`);
    await cdp.evaluate("[...document.querySelectorAll('.ds-modal-foot button')].find(button=>button.innerText.includes('移动知识表')).click()");
    await waitFor(() => cdp.evaluate("window.libraryFixture.state.moved==='/数学/复分析'"), 'move request recorded');

    await cdp.evaluate("[...document.querySelectorAll('.library-table-actions button')].find(button=>button.innerText.trim()==='删除表').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.ds-modal'))"), 'delete dialog');
    await cdp.evaluate("[...document.querySelectorAll('.ds-modal-foot button')].find(button=>button.innerText.includes('删除空表')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.ds-modal .ds-error')?.innerText.includes('表不为空')"), 'delete blocker explained');
    await cdp.evaluate("[...document.querySelectorAll('.ds-modal-foot button')].find(button=>button.innerText.trim()==='取消').click()");

    // Slow API: a loading state appears instead of a frozen window.
    await cdp.evaluate('window.libraryFixture.state.slow=true');
    await cdp.evaluate("[...document.querySelectorAll('.library-table-toolbar button')].find(button=>button.innerText.includes('刷新记录')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-loading-bar'))"), 'loading indicator during slow rows');
    await waitFor(() => cdp.evaluate("[...document.querySelectorAll('.library-table-toolbar button')].some(button=>button.innerText.includes('刷新记录'))"), 'slow load finished');
    await cdp.evaluate('window.libraryFixture.state.slow=false');

    // Permission loss is reported as permission, never as “missing data”.
    await cdp.evaluate('window.libraryFixture.state.denied=true');
    await cdp.evaluate("[...document.querySelectorAll('.library-table-toolbar button')].find(button=>button.innerText.includes('刷新记录')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.ds-error')?.innerText.includes('权限不足')"), 'permission error state');
    const deniedText = await cdp.evaluate("document.querySelector('.ds-error').innerText");
    assert.ok(!deniedText.includes('不存在'), 'permission errors are not disguised as missing data');
    await cdp.evaluate('window.libraryFixture.state.denied=false');
    await cdp.evaluate("[...document.querySelectorAll('.ds-error button')].find(button=>button.innerText.includes('重试')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.table-scroll tbody tr'))"), 'rows restored after retry');

    // Light theme capture for visual acceptance.
    await cdp.evaluate("document.documentElement.dataset.theme='light'");
    await sleep(150);
    await capture('xelibrary-table-light-1440x900');
    await cdp.evaluate("document.documentElement.dataset.theme='dark'");

    // Responsive: no horizontal document overflow at the required viewports.
    await cdp.evaluate("document.querySelector('.inspector-head button[aria-label=\"关闭 Inspector\"]')?.click()");
    await sleep(150);
    for (const [label, width, height] of [['1920x1080', 1920, 1080], ['1440x900', 1440, 900], ['1366x768', 1366, 768], ['1024x768', 1024, 768], ['390x844', 390, 844]]) {
      await cdp.call('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: width < 768});
      await sleep(250);
      const overflow = await cdp.evaluate('document.documentElement.scrollWidth - innerWidth');
      assert.ok(overflow <= 2, `no horizontal document overflow at ${label} (${overflow}px)`);
      await capture(`xelibrary-table-dark-${label}`);
    }

    const exceptions = cdp.events.filter(event => event.method === 'Runtime.exceptionThrown');
    assert.equal(exceptions.length, 0, `no uncaught exceptions: ${exceptions.map(event => event.params?.exceptionDetails?.text).join('; ')}`);
  } finally {
    cdp?.socket.close();
    try {
      await stopProcess(chrome);
    } finally {
      try {
        await server.close();
      } finally {
        await removeTree(tempDir);
      }
    }
  }
});
