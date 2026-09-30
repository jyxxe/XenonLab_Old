#!/usr/bin/env node
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import net from 'node:net';

const timeoutMs = 20_000;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const safeText = value => String(value).replace(/[\u0000-\u001f]/g, ' ').slice(0, 500);

function args() {
  const result = {};
  for (let i = 2; i < process.argv.length; i += 1) {
    if (process.argv[i] === '--config') result.config = process.argv[++i];
    else if (process.argv[i] === '--out') result.out = process.argv[++i];
    else if (process.argv[i] === '--chrome') result.chrome = process.argv[++i];
    else if (process.argv[i] === '--certutil') result.certutil = process.argv[++i];
    else if (process.argv[i] === '--ca-nickname') result.caNickname = process.argv[++i];
    else throw new Error(`Unknown argument ${process.argv[i]}`);
  }
  if (!result.config || !result.out) throw new Error('Usage: node scripts/browser_acceptance.mjs --config PRIVATE.json --out DIR [--chrome /path/to/chrome]');
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
  const config = await privateConfig(options.config, options.caNickname);
  const chromePath = options.chrome || '/usr/bin/google-chrome';
  const certutilPath = options.certutil || 'certutil';
  const caNickname = options.caNickname || config.caNickname;
  await fs.access(chromePath);
  // Check, but never modify, the Linux NSS database Chrome reads from the current HOME.
  // Certificate-error bypass flags are intentionally absent.
  verifyChromeCaTrust(certutilPath, caNickname);

  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xelonolab-browser-'));
  const profile = path.join(tempDir, 'chrome-profile');
  const outDir = path.resolve(options.out);
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
  const report = {checks: [], screenshots: [], consoleErrors: [], pageExceptions: [], httpErrors: [], overflow: []};
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
    cdp.on('Network.responseReceived', event => {
      if (event.response.status >= 400 && !event.response.url.startsWith('chrome-extension://')) {
        report.httpErrors.push({status: event.response.status, url: safeText(new URL(event.response.url).pathname)});
      }
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
    check('authenticated desktop and application dock', true);
    await measureOverflow('desktop');
    await screenshot('03-desktop.png');

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
      await cdp.evaluate("document.querySelector('.knowledge-sidebar button:has(strong)')?.click()");
      await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.table-scroll table'))"), 'knowledge table rows');
      const rowAvailable = await cdp.evaluate("Boolean(document.querySelector('.table-scroll tbody tr'))");
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

    check('browser console errors absent', report.consoleErrors.length === 0, `${report.consoleErrors.length} console error(s)`);
    check('uncaught page exceptions absent', report.pageExceptions.length === 0, `${report.pageExceptions.length} exception(s)`);
    report.httpErrors = report.httpErrors.filter(item => !item.url.endsWith('/favicon.ico'));
    check('HTTP error responses absent', report.httpErrors.length === 0, `${report.httpErrors.length} HTTP error response(s)`);
    await fs.writeFile(path.join(outDir, 'report.json'), `${JSON.stringify(report, null, 2)}\n`, {mode: 0o600});
    process.stdout.write(JSON.stringify({state: 'completed', screenshots: report.screenshots, checks: report.checks, report: path.join(outDir, 'report.json')}, null, 2) + '\n');
    if (report.checks.some(item => !item.passed)) process.exitCode = 1;
  } finally {
    cdp?.close();
    chrome.kill('SIGTERM');
    await sleep(250);
    if (chrome.exitCode === null) chrome.kill('SIGKILL');
    await fs.rm(tempDir, {recursive: true, force: true});
  }
}

main().catch(error => {
  // Keep arbitrary config and credential contents out of logs.
  process.stderr.write(`Browser acceptance failed: ${safeText(error.message)}\n`);
  process.exitCode = 1;
});
