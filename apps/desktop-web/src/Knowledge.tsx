import React,{useEffect,useState} from 'react';
type API=(path:string,init?:RequestInit)=>Promise<any>;
type Table={id:string;name:string;path:string};
type Row={_row_id:string;_revision:number;values:Record<string,any>};
export function Knowledge({api}:{api:API}){
 const [tables,setTables]=useState<Table[]>([]),[table,setTable]=useState<Table|null>(null),[rows,setRows]=useState<Row[]>([]);
 const [selected,setSelected]=useState<Row|null>(null),[columns,setColumns]=useState<any[]>([]),[values,setValues]=useState<Record<string,any>>({});
 const [message,setMessage]=useState(''),[filter,setFilter]=useState(''),[creating,setCreating]=useState(false),[name,setName]=useState(''),[path,setPath]=useState('/知识');
 async function load(){try{setTables(await api('/tables?limit=1000'))}catch(e){setMessage(String(e))}}
 useEffect(()=>{load()},[]);
 async function choose(t:Table){try{setTable(t);setSelected(null);setValues({});setRows(await api(`/tables/${t.id}/rows?limit=1000`));const schema=await api(`/tables/${t.id}/schema`);setColumns(schema.columns);setMessage('')}catch(e){setMessage(String(e))}}
 function select(row:Row|null){setSelected(row);setValues(row?{...row.values}:Object.fromEntries(columns.map(c=>[c.name,c.default_value])))}
 async function save(){if(!table)return;try{const endpoint=`/tables/${table.id}/rows${selected?'/'+selected._row_id:''}`;await api(endpoint,{method:selected?'PATCH':'POST',headers:selected?{'If-Match':`"revision:${selected._revision}"`}:{},body:JSON.stringify({values})});await choose(table);setMessage('已保存')}catch(e){setMessage(String(e))}}
 async function remove(){if(!table||!selected)return;try{await api(`/tables/${table.id}/rows/${selected._row_id}`,{method:'DELETE',headers:{'If-Match':`"revision:${selected._revision}"`}});await choose(table);setMessage('记录已软删除，历史仍保留')}catch(e){setMessage(String(e))}}
 async function create(){try{await api('/tables',{method:'POST',body:JSON.stringify({path,name,columns:[{name:'title',type:'string',nullable:false},{name:'body',type:'text'}]})});setCreating(false);await load();setMessage('知识表已创建')}catch(e){setMessage(String(e))}}
 function value(c:any,text:string){let result:any=text;if(['int64','float64'].includes(c.type))result=text===''?null:Number(text);if(['json','attachment_list'].includes(c.type)){try{result=JSON.parse(text)}catch{result=text}}setValues(v=>({...v,[c.name]:result}))}
 return <div className="knowledge"><div className="knowledge-sidebar"><h2>知识库</h2><input aria-label="筛选知识表" placeholder="搜索路径或表名" value={filter} onChange={e=>setFilter(e.target.value)}/><button onClick={()=>setCreating(!creating)}>新建知识表</button><button onClick={load}>刷新</button>
 {creating&&<div><label>路径<input value={path} onChange={e=>setPath(e.target.value)}/></label><label>表名<input value={name} onChange={e=>setName(e.target.value)}/></label><button disabled={!name} onClick={create}>创建</button></div>}
 {tables.filter(t=>(t.path+'/'+t.name).toLowerCase().includes(filter.toLowerCase())).map(t=><button className={table?.id===t.id?'active':''} key={t.id} onClick={()=>choose(t)}><strong>{t.name}</strong><small>{t.path}</small></button>)}</div>
 <div className="knowledge-content"><h2>{table?table.path+'/'+table.name:'选择知识表'}</h2><p role="status">{message}</p>{table&&<><button onClick={()=>select(null)}>新增记录</button><button onClick={()=>choose(table)}>刷新记录</button>
 <div className="table-scroll"><table><thead><tr>{columns.map(c=><th key={c.name}>{c.name}</th>)}</tr></thead><tbody>{rows.map(r=><tr key={r._row_id} onClick={()=>select(r)} className={selected?._row_id===r._row_id?'active':''}>{columns.map(c=><td key={c.name}>{typeof r.values[c.name]==='object'?JSON.stringify(r.values[c.name]):String(r.values[c.name]??'')}</td>)}</tr>)}</tbody></table></div>
 <section className="record-editor"><h3>{selected?'编辑记录 · revision '+selected._revision:'新增记录'}</h3>{columns.map(c=><label key={c.name}>{c.name} <small>{c.type}</small>{c.type==='boolean'?<select value={String(values[c.name]??'')} onChange={e=>setValues(v=>({...v,[c.name]:e.target.value===''?null:e.target.value==='true'}))}><option value="">空</option><option value="true">是</option><option value="false">否</option></select>:<textarea value={typeof values[c.name]==='object'&&values[c.name]!==null?JSON.stringify(values[c.name]):String(values[c.name]??'')} onChange={e=>value(c,e.target.value)}/>}</label>)}<button onClick={save}>保存记录</button>{selected&&<button onClick={remove}>软删除记录</button>}</section></>}</div></div>
}
