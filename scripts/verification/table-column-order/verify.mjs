import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import {chromium} from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const source=path.resolve(path.dirname(new URL(import.meta.url).pathname),'../../..'), app=path.join(source,'apps/desktop-web'), evidence=path.join(source,'runtime/verification/table-column-order');
await fs.mkdir(evidence,{recursive:true});
const fixture=path.join(app,'tests/.column-order-fixture.tsx'), html=path.join(app,'tests/.column-order-fixture.html');
let vite,browser; const checks=[],errors=[];
const push = checks.push.bind(checks); checks.push = (...values) => { console.log(...values); return push(...values); };
function parseTSV(text){const rows=[];let row=[],cell='',quoted=false;for(let i=0;i<text.length;i++){const char=text[i];if(char==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}else if(!quoted&&char==='\t'){row.push(cell);cell='';}else if(!quoted&&char==='\r'&&text[i+1]==='\n'){row.push(cell);rows.push(row);row=[];cell='';i++;}else cell+=char;}row.push(cell);rows.push(row);return rows;}
try {
 let text=await fs.readFile(path.join(app,'tests/library-fixture.tsx'),'utf8');
 // Only synthetic fixture data is extended; actual LibraryApp/TableView remain unchanged.
 text=text.replace('  calls,\n  state,','  calls,\n  columns: COLUMNS,\n  state,');
 text=text.replace("columns: [{name: 'note', type: 'text', nullable: true, default_value: null}]","columns: [{name: 'note', type: 'text', nullable: true, default_value: null}, {name: 'tag', type: 'string', nullable: true, default_value: null}]");
 text=text.replace("values: {note: '另一张表的记录'}","values: {note: '另一张表的记录', tag: 'other-tag'}");
 await fs.writeFile(fixture,text);
 await fs.writeFile(html,'<!doctype html><html><head><meta charset="UTF-8"></head><body><div id="root"></div><script type="module" src="/tests/.column-order-fixture.tsx"></script></body></html>');
 vite=await createServer({root:app,configFile:false,server:{host:'127.0.0.1',port:0,watch:null},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 const port=vite.httpServer.address().port;
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
 const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.tsvCopies=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.tsvCopies.push(text)}}});});
 await page.goto(`http://127.0.0.1:${port}/tests/.column-order-fixture.html`);
 async function open(name){await page.locator('.library-tree-table').filter({hasText:name}).click();await page.getByRole('heading',{name:new RegExp('/'+name+'$')}).waitFor();await page.locator('.library-table tbody tr[data-row-id]').first().waitFor();}
 const headers=()=>page.locator('.library-table thead .library-sort').allTextContents();
 const defaults=['Row ID','更新时间','title','body','type','confidence','files'];
 async function expectHeaders(names){await page.waitForFunction(names=>JSON.stringify([...document.querySelectorAll('.library-table thead .library-sort')].map(e=>e.textContent.trim().replace(/ [↑↓]$/,'')))===JSON.stringify(names),names,{timeout:10000});}
 await open('uniform-convergence');await expectHeaders(defaults);
 await page.locator('tr[data-row-id="row-001"]').click();assert.ok(await page.locator('.knowledge-inspector').count());
 await page.locator('.library-column-order summary').focus();await page.locator('.library-column-order summary').press('Enter');
 const callsBefore=await page.evaluate(()=>window.libraryFixture.calls.length);
 const move=page.getByRole('button',{name:'将 files 前移',exact:true});for(let i=0;i<8;i++)await page.keyboard.press('Tab');assert.equal(await move.evaluate(e=>document.activeElement===e),true);for(let i=0;i<4;i++){await page.keyboard.press('Enter');if(i<3)assert.equal(await move.evaluate(e=>document.activeElement===e),true);}
 const reordered=['Row ID','更新时间','files','title','body','type','confidence'];await expectHeaders(reordered);assert.equal(await move.isDisabled(),true);
 assert.equal(await page.evaluate(()=>window.libraryFixture.calls.length),callsBefore);
 const cells=await page.locator('tr[data-row-id="row-001"] td').allTextContents();assert.equal(cells[1],'row-001');assert.ok(cells[3].includes('proof.pdf'));assert.equal(cells[4],'一致收敛定义');assert.ok(cells.at(-1).includes('附件'));
 assert.equal(await page.locator('.library-table thead th').last().textContent(),'状态');
 const filesBounds=await page.locator('th').filter({has:page.locator('button[title="按 files 排序"]')}).boundingBox(),gridBounds=await page.getByRole('grid').boundingBox();assert.ok(filesBounds.x >= gridBounds.x && filesBounds.x+filesBounds.width <= gridBounds.x+gridBounds.width);
 await page.screenshot({path:path.join(evidence,'files-first-with-inspector.png'),fullPage:true});checks.push('actual keyboard four moves place files first; header/value binding, fixed system/status columns, Inspector and no API requests');
 // The base now ships PR #148's actual copySelected; exercise TSV copy under the reordered list.
 await page.locator('tr[data-row-id="row-001"] .library-td-select input[type="checkbox"]').check();
 const callsBeforeCopy=await page.evaluate(()=>window.libraryFixture.calls.length);
 await page.getByRole('button',{name:/复制已选记录为 TSV/}).click();
 await page.waitForFunction(()=>window.tsvCopies&&window.tsvCopies.length===1);
 const tsv=parseTSV(await page.evaluate(()=>window.tsvCopies[0]));
 assert.deepEqual(tsv[0],['Row ID','更新时间','files','title','body','type','confidence','状态']);
 assert.equal(tsv[1][0],'row-001');assert.ok(tsv[1][2].includes('proof.pdf'));assert.equal(tsv[1][3],'一致收敛定义');assert.ok(tsv[1].at(-1).includes('附件'));
 assert.equal(await page.evaluate(()=>window.libraryFixture.calls.length),callsBeforeCopy,'copy must not fetch records');
 await page.screenshot({path:path.join(evidence,'tsv-reordered-order.png'),fullPage:true});checks.push('reordered display order drives TSV headers and cells through the single gridColumns contract; fixed system/status columns and no API fetch');
 await page.reload();await open('uniform-convergence');await expectHeaders(reordered);checks.push('same-table localStorage order survives actual browser reload');
 await open('notes');await expectHeaders(['Row ID','更新时间','note','tag']);await page.locator('.library-column-order summary').click();await page.getByRole('button',{name:'将 tag 前移',exact:true}).click();await expectHeaders(['Row ID','更新时间','tag','note']);
 await open('uniform-convergence');await expectHeaders(reordered);await open('notes');await expectHeaders(['Row ID','更新时间','tag','note']);
 const saved=await page.evaluate(()=>({a:JSON.parse(localStorage.getItem('xeonolab.library.column-order.t1')),b:JSON.parse(localStorage.getItem('xeonolab.library.column-order.t2'))}));assert.deepEqual(saved,{a:['files','title','body','type','confidence'],b:['tag','note']});checks.push('A/B/A/B switching preserves independent orders; no cross-table hydrate/persist write');
 await page.evaluate(()=>{const columns=window.libraryFixture.columns;columns.splice(columns.findIndex(c=>c.name==='body'),1);columns.push({name:'new_field',type:'string',nullable:true});window.libraryFixture.state.rows[0].values.new_field='new-value';});
 await open('uniform-convergence');await expectHeaders(['Row ID','更新时间','files','title','type','confidence','new_field']);assert.equal(await page.locator('tr[data-row-id="row-001"] td').nth(7).textContent(),'new-value');checks.push('schema removal ignores stale preference and new field appends with correct cell binding');
 await page.locator('.library-column-order summary').click();await page.getByRole('button',{name:'恢复默认字段顺序',exact:true}).focus();await page.getByRole('button',{name:'恢复默认字段顺序',exact:true}).press('Enter');await expectHeaders(['Row ID','更新时间','title','type','confidence','files','new_field']);assert.equal(await page.evaluate(()=>localStorage.getItem('xeonolab.library.column-order.t1')),null);checks.push('keyboard default restore uses current schema, removes preference key and preserves other table preference');
 // Reorder again and resize files by its name; width remains attached to that field.
 await page.getByRole('button',{name:'将 files 前移',exact:true}).click();await page.getByRole('button',{name:'将 files 前移',exact:true}).click();await page.getByRole('button',{name:'将 files 前移',exact:true}).click();
 const filesHeader=page.locator('th').filter({has:page.locator('button[title="按 files 排序"]')});
 // The current base measures each header before resizing, so the expected width starts from the real header width.
 const startWidth=(await filesHeader.boundingBox()).width;
 const resize=page.getByRole('separator',{name:'调整 files 列宽',exact:true}),box=await resize.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+80,box.y+box.height/2);await page.mouse.up();
 const expectedWidth=Math.min(520,Math.max(80,startWidth+80));
 const width=await page.evaluate(()=>JSON.parse(localStorage.getItem('xeonolab.library.columns.t1')).files);assert.ok(Math.abs(width-expectedWidth)<1,`resized width ${width} tracks measured header ${startWidth}`);
 await page.getByRole('button',{name:'将 files 后移',exact:true}).click();const styleWidth=await filesHeader.evaluate(e=>e.style.width);assert.ok(Math.abs(parseFloat(styleWidth)-expectedWidth)<1);checks.push('real pointer resize follows field name through reordering and persists in existing per-table width key');
 await page.locator('.library-column-order summary').click();await page.locator('button[title="按 title 排序"]').click();await page.locator('th').filter({has:page.locator('button[title="按 title 排序"]')}).locator('.library-sort span[aria-hidden] svg').waitFor();const grid=page.getByRole('grid');await grid.focus();await grid.press('ArrowDown');await grid.press('Shift+ArrowDown');assert.equal(await page.locator('.library-table tr[aria-selected="true"]').count(),2);await grid.press('Enter');assert.ok(await page.locator('.knowledge-inspector').count());await grid.press('Control+a');const selToolbar=await page.locator('.library-table-toolbar').innerText();const loadedMatch=selToolbar.match(/(\d+) \/ (\d+) 行已加载/),selectedMatch=selToolbar.match(/已选 (\d+) 行/);assert.ok(loadedMatch&&selectedMatch,'Ctrl+A selection badge visible');assert.equal(selectedMatch[1],loadedMatch[1],'Ctrl+A selects all loaded rows');await grid.press('Escape');
 await grid.evaluate(e=>{e.scrollTop=e.scrollHeight;e.scrollLeft=e.scrollWidth;e.dispatchEvent(new Event('scroll'));});await page.locator('tr[data-row-id="row-001"]').waitFor();assert.ok(await page.locator('.library-table tbody tr[data-row-id]').count()<50);assert.ok(await grid.evaluate(e=>e.scrollLeft)>0);checks.push('sorting, Arrow/Shift/Enter/Ctrl+A/Escape selection, Inspector, horizontal scrolling and bounded virtual rows remain functional');
 await page.getByRole('button',{name:'加载更多记录',exact:true}).click();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await grid.evaluate(e=>{e.scrollTop=e.scrollHeight;e.dispatchEvent(new Event('scroll'));});await page.locator('tr[data-row-id="row-extra"]').waitFor();checks.push('explicit row pagination retains display preference and renders appended row');
 await page.screenshot({path:path.join(evidence,'ordered-columns-scroll-pagination.png'),fullPage:true});
 await page.setViewportSize({width:900,height:900});await page.getByRole('button',{name:'关闭 Inspector',exact:true}).click();assert.equal(await page.locator('.knowledge-inspector').count(),0);await page.locator('.library-column-order summary').focus();await page.locator('.library-column-order summary').press('Enter');await page.getByRole('button',{name:'将 new_field 前移',exact:true}).focus();await page.getByRole('button',{name:'将 new_field 前移',exact:true}).press('Enter');await page.screenshot({path:path.join(evidence,'keyboard-order-narrow.png'),fullPage:true});await expectHeaders(['Row ID','更新时间','title','files','type','new_field','confidence']);checks.push('900px existing floating Inspector is explicitly closed; visible order controls remain keyboard reachable with immediate correct reordering');
 await page.evaluate(()=>localStorage.setItem('xeonolab.library.column-order.t1','invalid-json'));await open('notes');await expectHeaders(['Row ID','更新时间','tag','note']);await open('uniform-convergence');await expectHeaders(['Row ID','更新时间','title','type','confidence','files','new_field']);assert.equal(await page.evaluate(()=>localStorage.getItem('xeonolab.library.column-order.t1')),'invalid-json');checks.push('invalid stored preference falls back to schema without hydration rewriting storage or affecting another table');
 assert.deepEqual(errors,[]);
 const sourceHashes={};for(const name of ['src/library/TableView.tsx','src/library/logic.js','src/library/library.css'])sourceHashes[name]=createHash('sha256').update(await fs.readFile(path.join(app,name))).digest('hex');
 const result={source,baseline:'3af21d3b6e8e4a23aa18ea05ddcad8140645f875',sourceHashes,checks,pageErrors:errors,saved,calls:await page.evaluate(()=>window.libraryFixture.calls),scope:'Actual LibraryApp/TableView/styles in Chrome; repository fixture 100 synthetic rows and modified two-table schema; all API and identity responses synthetic. localStorage actual browser persistence. No real backend/OIDC/production.'};await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}finally{if(browser)await browser.close();if(vite)await vite.close();await Promise.all([fixture,html].map(p=>fs.rm(p,{force:true})));}
