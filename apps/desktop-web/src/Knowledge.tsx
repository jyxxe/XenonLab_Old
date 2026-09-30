import React,{useEffect,useRef,useState} from 'react';
import {initialColumns,SchemaEditor,type Column} from './SchemaEditor';
import {Attachments} from './Attachments';

type API=(path:string,init?:RequestInit)=>Promise<any>;
type APIPage=(path:string,init?:RequestInit)=>Promise<{data:any;meta:{request_id?:string;next_cursor?:string|null;[key:string]:any}}>
type APIRaw=(path:string,init?:RequestInit)=>Promise<Response>;
type Table={id:string;name:string;path:string;table_revision?:number;schema_revision?:number};
type Row={_row_id:string;_revision:number;_created_at:string;_updated_at:string;_deleted_at:string|null;values:Record<string,any>};
type ScopeResponse={scopes:string[]};

export function Knowledge({api,apiPage,apiRaw}:{api:API;apiPage?:APIPage;apiRaw:APIRaw}){
 const [tables,setTables]=useState<Table[]>([]),[table,setTable]=useState<Table|null>(null),[rows,setRows]=useState<Row[]>([]);
 const [tableCursor,setTableCursor]=useState<string|null>(null),[rowsCursor,setRowsCursor]=useState<string|null>(null),[loadingTables,setLoadingTables]=useState(false),[loadingRows,setLoadingRows]=useState(false);
 const [selected,setSelected]=useState<Row|null>(null),[columns,setColumns]=useState<Column[]>([]),[values,setValues]=useState<Record<string,any>>({});
 const [message,setMessage]=useState(''),[filter,setFilter]=useState(''),[creating,setCreating]=useState(false),[name,setName]=useState(''),[path,setPath]=useState('/知识');
 const [scopes,setScopes]=useState<string[]>([]),[includeDeleted,setIncludeDeleted]=useState(false),[history,setHistory]=useState<Row[]>([]),[preview,setPreview]=useState<Row|null>(null);
 const [schemaDraft,setSchemaDraft]=useState<Column[]>(initialColumns.map(c=>({...c}))),[editingSchema,setEditingSchema]=useState(false),[destination,setDestination]=useState('');
 const [busy,setBusy]=useState(false);
 const activeTable=useRef(''),viewRequest=useRef(0),rowsRequest=useRef(0),historyRequest=useRef(0),tableListRequest=useRef(0),selectedRowKey=useRef(''),tableLoading=useRef(false);
 const canWrite=scopes.includes('xelibrary:write');
 const canAdmin=scopes.includes('xelibrary:admin');

 async function fetchTablePage(cursor=''){
  if(apiPage)return apiPage(`/tables?limit=100${cursor?'&cursor='+encodeURIComponent(cursor):''}`);
  return {data:await api('/tables?limit=1000'),meta:{next_cursor:null}};
 }
 async function fetchRowsPage(tableId:string,cursor='',showDeleted=includeDeleted){
  const query=new URLSearchParams({limit:apiPage?'100':'1000'});
  if(cursor)query.set('cursor',cursor);if(showDeleted)query.set('include_deleted','true');
  if(apiPage)return apiPage(`/tables/${tableId}/rows?${query.toString()}`);
  return {data:await api(`/tables/${tableId}/rows?${query.toString()}`),meta:{next_cursor:null}};
 }
 async function loadTables(append=false){
  if(tableLoading.current)return;
  const cursor=append?tableCursor:'';if(append&&!cursor)return;
  const request=++tableListRequest.current;tableLoading.current=true;setLoadingTables(true);
  try{const page=await fetchTablePage(cursor||'');if(request===tableListRequest.current){setTables(old=>append?[...old,...page.data.filter((item:Table)=>!old.some(existing=>existing.id===item.id))]:page.data);setTableCursor(page.meta.next_cursor||null)}}
  catch(e){if(request===tableListRequest.current)setMessage(String(e))}
  finally{if(request===tableListRequest.current){tableLoading.current=false;setLoadingTables(false)}}
 }
 useEffect(()=>{void loadTables();api('/users/me').then((me:ScopeResponse)=>setScopes(me.scopes||[])).catch(e=>setMessage(String(e)))},[]);

 async function choose(t:Table){
  activeTable.current=t.id;selectedRowKey.current='';const request=++viewRequest.current;++rowsRequest.current;++historyRequest.current;
  setTable(t);setSelected(null);setValues({});setRows([]);setRowsCursor(null);setLoadingRows(false);setColumns([]);setSchemaDraft([]);setHistory([]);setPreview(null);setEditingSchema(false);setDestination(t.path);setMessage('');
  try{
   const [rowsPage,schema]=await Promise.all([
    fetchRowsPage(t.id,'',includeDeleted),
    api(`/tables/${t.id}/schema`),
   ]);
   if(activeTable.current!==t.id||viewRequest.current!==request)return;
   setRows(rowsPage.data);setRowsCursor(rowsPage.meta.next_cursor||null);setColumns(schema.columns);setSchemaDraft(schema.columns);setTable({...t,...schema});setMessage('');
  }catch(e){if(activeTable.current===t.id&&viewRequest.current===request)setMessage(String(e))}
 }
 async function refreshRows(tableId:string,showDeleted=includeDeleted){
  const request=++rowsRequest.current,view=viewRequest.current;
  setRowsCursor(null);setLoadingRows(false);
  try{const page=await fetchRowsPage(tableId,'',showDeleted);if(activeTable.current===tableId&&rowsRequest.current===request&&viewRequest.current===view){setRows(page.data);setRowsCursor(page.meta.next_cursor||null)}}catch(e){if(activeTable.current===tableId&&rowsRequest.current===request&&viewRequest.current===view)setMessage(String(e))}
 }
 async function loadMoreRows(){
  if(!table||!rowsCursor||loadingRows)return;const tableId=table.id,cursor=rowsCursor,view=viewRequest.current,request=++rowsRequest.current;setLoadingRows(true);
  try{const page=await fetchRowsPage(tableId,cursor);if(activeTable.current===tableId&&viewRequest.current===view&&rowsRequest.current===request){setRows(old=>[...old,...page.data.filter((item:Row)=>!old.some(existing=>existing._row_id===item._row_id))]);setRowsCursor(page.meta.next_cursor||null)}}catch(e){if(activeTable.current===tableId&&viewRequest.current===view&&rowsRequest.current===request)setMessage(String(e))}finally{if(activeTable.current===tableId&&viewRequest.current===view&&rowsRequest.current===request)setLoadingRows(false)}
 }
 async function refreshTableMetadata(tableId:string,view=viewRequest.current){
  try{
   const metadata=await api(`/tables/${tableId}/schema`);
   if(activeTable.current!==tableId||viewRequest.current!==view)return;
   setTable(current=>current?.id===tableId?{...current,...metadata}:current);
   setColumns(current=>metadata.columns||current);
   if(!editingSchema)setSchemaDraft(metadata.columns||[]);
  }catch(e){if(activeTable.current===tableId&&viewRequest.current===view)setMessage(String(e))}
 }
 async function loadHistory(row:Row){
  const tableId=table?.id;if(!tableId)return;
  const key=`${tableId}:${row._row_id}`;selectedRowKey.current=key;const request=++historyRequest.current;
  try{const result=await api(`/tables/${tableId}/rows/${row._row_id}/revisions`);if(selectedRowKey.current===key&&historyRequest.current===request)setHistory(result)}catch(e){if(selectedRowKey.current===key&&historyRequest.current===request)setMessage(String(e))}
 }
 function select(row:Row|null){
  ++historyRequest.current;selectedRowKey.current=row&&table?`${table.id}:${row._row_id}`:'';
  setSelected(row);setPreview(null);setHistory([]);setValues(row?{...row.values}:Object.fromEntries(columns.map(c=>[c.name,c.default_value])));
  if(row)void loadHistory(row);
 }
 function value(c:Column,text:string){
  let result:any=text;
  if(['int64','float64'].includes(c.type))result=text===''?null:Number(text);
  if(['json','attachment_list'].includes(c.type)&&text!==''){try{result=JSON.parse(text)}catch{result=text}}
  setValues(v=>({...v,[c.name]:result}));
 }
 async function saveRow(){
  if(!table||!canWrite)return;const current=table,view=viewRequest.current,selectedKey=selectedRowKey.current;setBusy(true);
  try{
   const endpoint=`/tables/${current.id}/rows${selected?'/'+selected._row_id:''}`;
   const result:Row=await api(endpoint,{method:selected?'PATCH':'POST',headers:selected?{'If-Match':`"revision:${selected._revision}"`}:{},body:JSON.stringify({values})});
   if(activeTable.current===current.id&&viewRequest.current===view&&selectedRowKey.current===selectedKey){setSelected(result);setValues({...result.values});setPreview(null);void loadHistory(result);}
   await Promise.all([refreshRows(current.id),refreshTableMetadata(current.id,view)]);if(activeTable.current===current.id&&viewRequest.current===view)setMessage('已保存');
  }catch(e){if(activeTable.current===current.id&&viewRequest.current===view)setMessage(String(e))}finally{setBusy(false)}
 }
 async function removeRow(){
  if(!table||!selected||!canWrite)return;const current=table,view=viewRequest.current,selectedKey=selectedRowKey.current;setBusy(true);
  try{
   await api(`/tables/${current.id}/rows/${selected._row_id}`,{method:'DELETE',headers:{'If-Match':`"revision:${selected._revision}"`}});
   if(activeTable.current===current.id&&viewRequest.current===view&&selectedRowKey.current===selectedKey){setSelected(null);setValues({});setHistory([]);setPreview(null);selectedRowKey.current='';}
   await Promise.all([refreshRows(current.id),refreshTableMetadata(current.id,view)]);if(activeTable.current===current.id&&viewRequest.current===view)setMessage('记录已软删除，历史仍保留');
  }catch(e){if(activeTable.current===current.id&&viewRequest.current===view)setMessage(String(e))}finally{setBusy(false)}
 }
 async function restore(revision:number){
  if(!table||!selected||!canWrite)return;const current=table,view=viewRequest.current,selectedKey=selectedRowKey.current;setBusy(true);
  try{
   const result:Row=await api(`/tables/${current.id}/rows/${selected._row_id}/restore`,{method:'POST',headers:{'If-Match':`"revision:${selected._revision}"`},body:JSON.stringify({revision})});
   if(activeTable.current===current.id&&viewRequest.current===view&&selectedRowKey.current===selectedKey){setSelected(result);setValues({...result.values});setPreview(null);void loadHistory(result);}
   await Promise.all([refreshRows(current.id),refreshTableMetadata(current.id,view)]);if(activeTable.current===current.id&&viewRequest.current===view)setMessage('已恢复到 revision '+revision);
  }catch(e){if(activeTable.current===current.id&&viewRequest.current===view)setMessage(String(e))}finally{setBusy(false)}
 }
 async function createTable(){
  if(!name.trim()||!canWrite)return;const activeBefore=activeTable.current;setBusy(true);
  try{
   const result=await api('/tables',{method:'POST',body:JSON.stringify({path,name:name.trim(),columns:schemaDraft})});
   const created:Table={id:result.table_id,name:name.trim(),path:result.path,table_revision:result.revision,schema_revision:1};
   setCreating(false);setName('');setTables(old=>[created,...old.filter(item=>item.id!==created.id)]);
   if(activeTable.current===activeBefore){await choose(created);if(activeTable.current===created.id)setMessage('知识表已创建')}
   else setMessage('知识表已创建');
  }catch(e){setMessage(String(e))}finally{setBusy(false)}
 }
 async function saveSchema(){
  if(!table||!canAdmin||!Number.isInteger(table.table_revision))return;const current=table;setBusy(true);
  try{
   await api(`/tables/${current.id}/schema`,{method:'PATCH',headers:{'If-Match':`"revision:${current.table_revision}"`},body:JSON.stringify({columns:schemaDraft})});
   if(activeTable.current===current.id){setEditingSchema(false);await choose(current);if(activeTable.current===current.id)setMessage('字段定义已更新')}
  }catch(e){if(activeTable.current===current.id)setMessage(String(e))}finally{setBusy(false)}
 }
 async function moveTable(){
  if(!table||!canWrite||!Number.isInteger(table.table_revision)||destination===table.path)return;const current=table;setBusy(true);
  try{
   const result=await api(`/tables/${current.id}/move`,{method:'POST',headers:{'If-Match':`"revision:${current.table_revision}"`},body:JSON.stringify({path:destination})});
   const moved={...current,path:result.path};setTables(old=>old.map(item=>item.id===current.id?moved:item));if(activeTable.current===current.id){setTable(moved);await choose(moved);setMessage('知识表已移动')}
  }catch(e){if(activeTable.current===current.id)setMessage(String(e))}finally{setBusy(false)}
 }

 return <div className="knowledge"><div className="knowledge-sidebar"><h2>知识库</h2><input aria-label="筛选知识表" placeholder="搜索路径或表名" value={filter} onChange={e=>setFilter(e.target.value)}/>
  <button disabled={!canWrite} title={!canWrite?'需要 xelibrary:write 权限':''} onClick={()=>{setCreating(!creating);if(!creating)setSchemaDraft(initialColumns.map(c=>({...c})));setMessage('')}}>新建知识表</button><button disabled={loadingTables} onClick={()=>void loadTables()}>{loadingTables?'加载中…':'刷新'}</button>
  {creating&&<div className="knowledge-create"><label>路径<input value={path} onChange={e=>setPath(e.target.value)}/></label><label>表名<input value={name} onChange={e=>setName(e.target.value)}/></label><SchemaEditor columns={schemaDraft} change={setSchemaDraft} disabled={busy||!canWrite}/><button disabled={!canWrite||busy||!name.trim()} onClick={()=>void createTable()}>创建知识表</button><button disabled={busy} onClick={()=>setCreating(false)}>取消</button></div>}
  {tables.filter(t=>(t.path+'/'+t.name).toLowerCase().includes(filter.toLowerCase())).map(t=><button className={table?.id===t.id?'active':''} key={t.id} onClick={()=>void choose(t)}><strong>{t.name}</strong><small>{t.path}</small></button>)}
  {tableCursor&&<button disabled={loadingTables} onClick={()=>void loadTables(true)}>{loadingTables?'加载中…':'加载更多知识表'}</button>}
 </div><div className="knowledge-content"><h2>{table?table.path+'/'+table.name:'选择知识表'}</h2><p role="status" aria-live="polite">{message}</p>{table&&<>
  <div className="knowledge-actions"><button disabled={!canWrite} onClick={()=>select(null)}>新增记录</button><button disabled={busy} onClick={()=>void choose(table)}>刷新记录</button><label><input type="checkbox" checked={includeDeleted} onChange={e=>{const next=e.target.checked;setIncludeDeleted(next);if(!next&&selected?._deleted_at)select(null);void refreshRows(table.id,next)}}/>显示已软删除记录</label></div>
  <section className="table-management"><strong>字段</strong><span>{columns.map(c=>`${c.name} · ${c.type}${c.nullable?' · 可空':''}`).join('，')}</span>{canAdmin&&<button disabled={busy||!Number.isInteger(table.table_revision)} onClick={()=>{setSchemaDraft(columns.map(c=>({...c})));setEditingSchema(!editingSchema)}}>{editingSchema?'取消编辑字段':'修改字段'}</button>}
   {editingSchema&&<div className="schema-management"><SchemaEditor columns={schemaDraft} change={setSchemaDraft} disabled={busy}/><button disabled={busy||!canAdmin||!Number.isInteger(table.table_revision)} onClick={()=>void saveSchema()}>保存字段定义</button></div>}
   <div className="move-table"><label>移动到路径<input disabled={!canWrite||!Number.isInteger(table.table_revision)||busy} value={destination} onChange={e=>setDestination(e.target.value)}/></label><button disabled={!canWrite||busy||!Number.isInteger(table.table_revision)||destination===table.path} onClick={()=>void moveTable()}>移动知识表</button></div>
  </section>
  <div className="table-scroll"><table><thead><tr>{columns.map(c=><th key={c.name}>{c.name}</th>)}<th>状态</th><th>Revision</th></tr></thead><tbody>{rows.map(r=><tr key={r._row_id} onClick={()=>select(r)} className={selected?._row_id===r._row_id?'active':''}>{columns.map(c=><td key={c.name}>{typeof r.values[c.name]==='object'&&r.values[c.name]!==null?JSON.stringify(r.values[c.name]):String(r.values[c.name]??'')}</td>)}<td>{r._deleted_at?'已软删除':'正常'}</td><td>{r._revision}</td></tr>)}</tbody></table>{!rows.length&&<p>没有可显示的记录。</p>}</div>{rowsCursor&&<button disabled={loadingRows} onClick={()=>void loadMoreRows()}>{loadingRows?'加载中…':'加载更多记录'}</button>}
  {selected&&<section className="record-editor"><h3>{selected._deleted_at?'已软删除记录':'记录'} · revision {selected._revision}</h3>{columns.map(c=><label key={c.name}>{c.name} <small>{c.type}</small>{c.type==='boolean'?<select disabled={!canWrite||Boolean(selected._deleted_at)||busy} value={String(values[c.name]??'')} onChange={e=>setValues(v=>({...v,[c.name]:e.target.value===''?null:e.target.value==='true'}))}><option value="">空</option><option value="true">是</option><option value="false">否</option></select>:<textarea disabled={!canWrite||Boolean(selected._deleted_at)||busy} value={typeof values[c.name]==='object'&&values[c.name]!==null?JSON.stringify(values[c.name]):String(values[c.name]??'')} onChange={e=>value(c,e.target.value)}/>}</label>)}
   {!selected._deleted_at&&<><button disabled={!canWrite||busy} onClick={()=>void saveRow()}>保存记录</button><button disabled={!canWrite||busy} onClick={()=>void removeRow()}>软删除记录</button></>}
   {!selected._deleted_at&&table&&<Attachments key={`${table.id}:${selected._row_id}`} tableId={table.id} rowId={selected._row_id} api={api} apiPage={apiPage} apiRaw={apiRaw} canAttach={scopes.includes('xelibrary:attach')} onChanged={()=>refreshTableMetadata(table.id)}/>}
   <section className="record-history"><h4>记录历史</h4>{history.map(revision=><article key={revision._revision}><strong>Revision {revision._revision} · {revision._deleted_at?'已软删除':'有效'}</strong><small>{revision._updated_at}</small><pre>{JSON.stringify(revision.values,null,2)}</pre><button disabled={busy||revision._revision===selected._revision} onClick={()=>setPreview(revision)}>查看</button><button disabled={!canWrite||busy||revision._revision===selected._revision} onClick={()=>void restore(revision._revision)}>恢复此版本</button></article>)}{!history.length&&<p>暂无历史记录。</p>}{preview&&<p role="status">正在查看 revision {preview._revision}。恢复会新增一个 revision，并保留当前历史。</p>}</section>
  </section>}
 </>}</div></div>
}
