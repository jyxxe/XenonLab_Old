import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '../../..');
const appRoot = path.join(repoRoot, 'apps/desktop-web');
const depsAppRoot = appRoot;
const fixturePath = path.join(here, 'shell-fixture.tsx');
const evidence=path.join(repoRoot,'runtime/verification/issue168-chat-focus',process.env.CHAT_FOCUS_EXPECT === 'before' ? 'before' : 'after');await fs.mkdir(evidence,{recursive:true});
const {createServer} = await import(pathToFileURL(path.join(depsAppRoot, 'node_modules/vite/dist/node/index.js')));
const {chromium} = await import(pathToFileURL(path.join(depsAppRoot, 'node_modules/@playwright/test/index.mjs')));
const chatServer = http.createServer((req,res)=>{res.writeHead(200,{'Content-Type':'text/html'});res.end('<!doctype html><textarea aria-label="Chat input"></textarea>')});
await new Promise(resolve=>chatServer.listen(0,'127.0.0.1',resolve));
const chatOrigin=`http://127.0.0.1:${chatServer.address().port}`;
const server=await createServer({root:appRoot,configFile:false,server:{host:'127.0.0.1',port:0,strictPort:false,watch:null,fs:{allow:[repoRoot]}},resolve:{alias:{react:path.join(depsAppRoot,'node_modules/react'),'react-dom':path.join(depsAppRoot,'node_modules/react-dom')}},plugins:[{name:'legacy-handoff-before',enforce:'pre',transform(code,id){
 if(process.env.CHAT_FOCUS_EXPECT!=='before'||!id.split('?')[0].endsWith('/src/library/LibraryApp.tsx'))return;
 const start=code.indexOf('  // Modal passive cleanup'),end=code.indexOf('  const catalogueError',start);
 assert.ok(start>=0&&end>start);
 return code.slice(0,start)+`  function confirmAskAI(prompt: string) {
    setAskAI(null);
    if (!chatApp) return;
    if (openApp) openApp('chat', {action: 'askAI', prompt});
    if (notify) notify({title: 'AI 草稿已投递到对话窗口，请核对后发送。', tone: 'info'});
  }

`+code.slice(end);
 }},{name:'round18-fixture-page',configureServer(vite){vite.middlewares.use((req,res,next)=>{if(req.url!=='/__round18.html')return next();res.setHeader('Content-Type','text/html');res.end(`<!doctype html><html><head><meta charset="utf-8"></head><body><div id="root"></div><script>globalThis.__XEONOLAB_CHAT_ORIGIN__=${JSON.stringify(chatOrigin)};</script><script type="module" src="/@fs${fixturePath}"></script></body></html>`);});}}]});
let browser;
try {
 await server.listen();
 const origin=`http://127.0.0.1:${server.httpServer.address().port}`;
 browser=await chromium.launch({executablePath:process.env.CHROME_BIN||chromium.executablePath(),headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 async function runCase(name, initialChat) {
   const context=await browser.newContext({viewport:{width:1440,height:960}});
   await context.grantPermissions(['clipboard-read','clipboard-write'],{origin});
   await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:chatOrigin});
   const page=await context.newPage(); const pageErrors=[]; page.on('pageerror',e=>pageErrors.push(String(e)));
   await page.goto(`${origin}/__round18.html`);
   await page.locator('nav.dock button[title="知识库"]').waitFor();
   const initialWindows=await page.locator('.window').evaluateAll(ws=>ws.map(w=>w.getAttribute('data-app-id')));
   assert.deepEqual(initialWindows,[],'fixture begins with no restored windows');
   if(initialChat){
     await page.locator('nav.dock button[title="AI 对话"]').click();
     await page.locator('.window[data-app-id="chat"]').waitFor();
     if(initialChat==='minimized') await page.locator('.window[data-app-id="chat"] button[aria-label^="最小化"]').click();
   }
   await page.locator('nav.dock button[title="知识库"]').click();
   await page.locator('.window[data-app-id="knowledge"][data-window-active="true"]').waitFor();
   await page.locator('.knowledge-sidebar .library-tree-table').filter({hasText:'research-notes'}).click();
   const row=page.locator('.table-scroll tbody tr').first(); await row.waitFor(); await row.click();
   await page.locator('.record-editor').waitFor();
   const sourceRow=await page.locator('.record-editor').innerText();
   const before=await page.locator('.window').evaluateAll(ws=>ws.map(w=>({app:w.getAttribute('data-app-id'),active:w.getAttribute('data-window-active'),hidden:getComputedStyle(w).visibility==='hidden'})));
   await page.locator('.library-table-actions button').filter({hasText:'Ask AI'}).click();
   for(const close of ['cancel','escape']){
     if(close==='cancel')await page.getByRole('button',{name:'取消',exact:true}).click();else await page.keyboard.press('Escape');
     assert.equal(await page.getByRole('dialog').count(),0);
     assert.equal(await page.locator('.window[data-window-active="true"]').getAttribute('data-app-id'),'knowledge');
     assert.equal(await page.evaluate(()=>document.activeElement?.textContent?.trim()),'Ask AI');
     await page.locator('.library-table-actions button').filter({hasText:'Ask AI'}).click();
   }
   await page.getByRole('textbox',{name:'你的问题'}).fill('Summarize the selected fixture note.');
   await page.waitForFunction(()=>document.querySelector('.library-ask-editor')?.value.includes('问题：Summarize the selected fixture note.'));
   const prompt=await page.locator('.library-ask-editor').inputValue();
   await page.getByRole('button',{name:'发送到 AI 对话'}).click();
   const card=page.getByRole('region',{name:'待发送的 AI 草稿'}); await card.waitFor();
   await page.locator('.iframe-toolbar').filter({hasText:'页面已载入'}).waitFor();
   assert.equal(await card.getByRole('textbox',{name:'AI 草稿内容'}).inputValue(),prompt);
   const postStack=await page.locator('.window').evaluateAll(ws=>ws.map(w=>({app:w.getAttribute('data-app-id'),active:w.getAttribute('data-window-active'),hidden:getComputedStyle(w).visibility==='hidden',z:getComputedStyle(w).zIndex})));
   const active=await page.locator('.window[data-window-active="true"]').getAttribute('data-app-id');
   const chatHidden=await page.locator('.window[data-app-id="chat"]').evaluate(w=>getComputedStyle(w).visibility==='hidden');
   const copyBox=await card.getByRole('button',{name:'复制草稿'}).boundingBox();
   const clickOwner=await page.evaluate(({x,y})=>{const e=document.elementFromPoint(x,y);return {tag:e?.tagName,text:(e?.textContent||'').slice(0,80),app:e?.closest('.window')?.getAttribute('data-app-id')}},{x:copyBox.x+copyBox.width/2,y:copyBox.y+copyBox.height/2});
   let copyReachable=true, clickErr='';
   if(!chatHidden && active==='chat') { await card.getByRole('button',{name:'复制草稿'}).click({timeout:1500}).catch(e=>{copyReachable=false;clickErr=String(e)}); }
   else { copyReachable=false; try { await card.getByRole('button',{name:'复制草稿'}).click({timeout:1000}); } catch(e){clickErr=String(e)} }
   if(!copyReachable) await fs.writeFile(path.join(evidence,`${name}-obstructed.png`),await page.screenshot({fullPage:true}));
   let recovered=false;
   if(!copyReachable){await page.locator('nav.dock button[title="AI 对话"]').click(); await page.locator('.window[data-app-id="chat"][data-window-active="true"]').waitFor(); await card.getByRole('button',{name:'复制草稿'}).click(); recovered=true;}
   const copied=await page.evaluate(()=>navigator.clipboard.readText());
   if(process.env.CHAT_FOCUS_EXPECT==='before'){assert.equal(active,'knowledge');assert.equal(copyReachable,false);assert.equal(clickOwner.app,'knowledge');}
   else{assert.equal(active,'chat');assert.equal(chatHidden,false);assert.equal(copyReachable,true);assert.equal(clickOwner.app,'chat');assert.equal(recovered,false);assert.ok((await card.innerText()).includes('尚未发送'));await fs.writeFile(path.join(evidence,`${name}-handoff.png`),await page.screenshot({fullPage:true}));const chatFrame=page.frame({url:chatOrigin+'/'});assert.ok(chatFrame);assert.equal(await chatFrame.getByRole('textbox',{name:'Chat input'}).inputValue(),'');await card.getByRole('button',{name:'收起',exact:true}).click();assert.equal(await card.count(),0);await chatFrame.getByRole('textbox',{name:'Chat input'}).click();await page.keyboard.press('Control+V');assert.equal(await chatFrame.getByRole('textbox',{name:'Chat input'}).inputValue(),prompt);}
   assert.equal(copied,prompt);
   const selection=await page.locator('.table-scroll tbody tr.is-active-row').getAttribute('data-row-id');
   assert.equal(pageErrors.length,0);
   const result={cancelAndEscapeRestore:true,nativeClipboard:true,manualPaste:process.env.CHAT_FOCUS_EXPECT!=='before',name,initialChat,initialWindows,before,postStack,active,chatHidden,copyReachable,clickErr,copyBox,clickOwner,recovered,promptLength:prompt.length,copiedMatches:copied===prompt,selection,sourceRow,pageErrors};
   await fs.writeFile(path.join(evidence,`${name}.png`),await page.screenshot({fullPage:true}));
   await context.close(); return result;
 }
 const results=[];
 results.push(await runCase('new-chat',null));
 results.push(await runCase('existing-chat','visible'));
 results.push(await runCase('minimized-chat','minimized'));
 const report={outcome:'PASS',mode:process.env.CHAT_FOCUS_EXPECT==='before'?'legacy synchronous handoff reproduction':'fixed passive-effect handoff',source:'Reproduction uses the current repository DesktopShell, WindowFrame, LibraryApp, and IFrameApp rendered by local Vite in real headless Chromium. The API is a local in-memory synthetic fixture with UUID table/row IDs and contract-shaped responses. The chat iframe is a loopback synthetic page; no production identity, data, remote writes, or OpenWebUI are used.',command:'node scripts/verification/chat-focus/verify.mjs',results,conclusion:'Compare the new-chat, existing-visible-chat, and minimized-chat normal paths to determine whether the confirmed handoff focuses Chat and leaves its unsent draft controls usable. Chat delivery remains an explicitly local test double.'};
 await fs.writeFile(path.join(evidence,'independent-browser-report.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({report:path.join(evidence,'independent-browser-report.json'),results},null,2));
} finally {if(browser)await browser.close(); await server.close(); await new Promise(resolve=>chatServer.close(resolve));}
