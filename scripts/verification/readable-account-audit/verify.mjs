import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import {chromium} from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const root=process.cwd(),app=path.join(root,'apps/desktop-web'),source=path.join(root,'scripts/verification/readable-account-audit'),out=path.join(root,'runtime/verification/issue175-readable-account-audit');
const tsx=path.join(app,'__agent_account_audit_fixture.tsx'),html=path.join(app,'__agent_account_audit_fixture.html');
await fs.mkdir(out,{recursive:true});await fs.copyFile(path.join(source,'fixture.tsx'),tsx);await fs.copyFile(path.join(source,'fixture.html'),html);
let server,browser;
try{
 server=await createServer({root:app,server:{host:'127.0.0.1',port:0,watch:null}});await server.listen();
 const address=server.httpServer.address();browser=await chromium.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1280,height:960}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${address.port}/__agent_account_audit_fixture.html`);
 await page.getByRole('heading',{name:'我的账号树'}).waitFor();
 async function read(){await page.locator('summary').filter({hasText:'账号审计'}).click();await page.getByRole('button',{name:'读取最近记录',exact:true}).click();await page.getByText('操作人：根账号（root.login） · synthetic-root',{exact:true}).first().waitFor();}
 await read();const rows=page.locator('details').filter({has:page.locator('summary').filter({hasText:'账号审计'})}).locator('li');assert.equal(await rows.count(),4);
 assert.ok((await rows.nth(0).innerText()).includes('目标：同名账号（child.a） · synthetic-child-a'));
 assert.ok((await rows.nth(1).innerText()).includes('操作人：同名账号（child.b） · synthetic-child-b'));
 assert.ok((await rows.nth(1).innerText()).includes('目标：同名账号（child.a） · synthetic-child-a'));
 assert.ok((await rows.nth(2).innerText()).includes('操作人：synthetic-outside'));assert.ok((await rows.nth(3).innerText()).includes('目标：synthetic-missing'));assert.ok(!(await rows.nth(2).locator('div').first().innerText()).includes('同名账号'));
 assert.equal(await rows.nth(0).evaluate(node=>Array.from(node.childNodes).filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('')),await page.evaluate(()=>new Date(1790960039*1000).toLocaleString()+' · account.updated'));
 const before=await rows.allInnerTexts();await page.evaluate(()=>window.auditFixture.failAudit=true);await page.getByRole('button',{name:'读取最近记录',exact:true}).click();await page.getByText('synthetic audit unavailable',{exact:true}).waitFor();assert.deepEqual(await rows.allInnerTexts(),before);
 await page.evaluate(()=>window.auditFixture.failAudit=false);await page.getByRole('button',{name:'读取最近记录',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[role="status"]')?.textContent==='');assert.deepEqual(await rows.allInnerTexts(),before);
 await page.screenshot({path:path.join(out,'readable-account-audit.png'),fullPage:true});
 await page.getByRole('button',{name:'测试：重新载入当前账号树',exact:true}).click();await page.getByRole('heading',{name:'我的账号树'}).waitFor();await read();assert.ok((await rows.nth(0).innerText()).includes('目标：更新后的账号（child.a） · synthetic-child-a'));assert.ok((await rows.nth(1).innerText()).includes('操作人：同名账号（child.b） · synthetic-child-b'));
 await page.setViewportSize({width:390,height:844});await rows.first().scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'readable-account-audit-mobile.png'),fullPage:true});
 const calls=await page.evaluate(()=>window.auditFixture.calls);assert.ok(calls.every(c=>c.method==='GET'));assert.ok(calls.every(c=>c.path==='/users/me'||c.path==='/auth/accounts/root-scope-candidates'||c.path.startsWith('/auth/accounts?')||c.path.startsWith('/auth/api-keys?')||c.path==='/auth/accounts/audit/events'));assert.equal(calls.filter(c=>c.path==='/auth/accounts/audit/events').length,4);assert.deepEqual(errors,[]);
 const report={baseline:'84d9f3b',source:'Actual Accounts component in local Chrome with in-memory synthetic account tree/audit GET responses. Tree reload uses a fixture-only remount; B is the human manager/parent of A with inherited management scopes; unknown-ID rows test GET shape/OR visibility only, not successful management mutations. No production identities, audit records or API.',checks:['known actor and target names/login/raw ID','duplicate display names resolved by exact ID','unknown IDs retained without false attribution','timestamp/event and event count preserved','read failure preserves previous audit rows and retry recovers','current tree reload updates current name','existing GET routes only; no writes or identity lookup','390px layout'],calls,pageErrors:errors,outcome:'PASS'};
 await fs.writeFile(path.join(out,'browser-report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
}finally{await browser?.close();await server?.close();await fs.rm(tsx,{force:true});await fs.rm(html,{force:true});}
