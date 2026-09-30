import React,{useEffect,useRef,useState} from 'react';

type API=(path:string,init?:RequestInit)=>Promise<any>;
type APIPage=(path:string,init?:RequestInit)=>Promise<{data:any;meta:{[key:string]:any}}>
type APIRaw=(path:string,init?:RequestInit)=>Promise<Response>;
type Attachment={attachment_id:string;row_id:string;filename:string;media_type:string;size:number;created_at:string;metadata:Record<string,any>};

export function Attachments({tableId,rowId,api,apiPage,apiRaw,canAttach,onChanged}:{tableId:string;rowId:string;api:API;apiPage?:APIPage;apiRaw:APIRaw;canAttach:boolean;onChanged:()=>Promise<void>}){
 const [items,setItems]=useState<Attachment[]>([]),[message,setMessage]=useState(''),[sourceId,setSourceId]=useState(''),[busy,setBusy]=useState(false);
 const request=useRef(0),active=useRef('');
 const key=`${tableId}:${rowId}`;
 async function load(){
  const current=++request.current;
  try{
   const result=apiPage?await apiPage(`/tables/${tableId}/rows/${rowId}/attachments`):{data:await api(`/tables/${tableId}/rows/${rowId}/attachments`)};
   if(active.current===key&&request.current===current){setItems(result.data);setMessage('')}
  }catch(e){if(active.current===key&&request.current===current)setMessage(String(e))}
 }
 useEffect(()=>{active.current=key;void load();return()=>{active.current='';++request.current}},[tableId,rowId]);
 async function upload(file:File){
  const form=new FormData();form.append('upload',file);
  setBusy(true);setMessage('');
  try{
   const response=await apiRaw(`/attachments?table_id=${encodeURIComponent(tableId)}&row_id=${encodeURIComponent(rowId)}`,{method:'POST',body:form});
   await response.json();
   if(active.current!==key)return;await load();if(active.current!==key)return;await onChanged();if(active.current===key)setMessage('附件已上传并关联到当前记录');
  }catch(e){if(active.current===key)setMessage(String(e))}finally{if(active.current===key)setBusy(false)}
 }
 async function bind(){
  if(!sourceId.trim())return;setBusy(true);setMessage('');
  try{
   await api(`/tables/${tableId}/rows/${rowId}/attachments`,{method:'POST',body:JSON.stringify({attachment_id:sourceId.trim()})});
   if(active.current!==key)return;setSourceId('');await load();if(active.current!==key)return;await onChanged();if(active.current===key)setMessage('附件已关联到当前记录');
  }catch(e){if(active.current===key)setMessage(String(e))}finally{if(active.current===key)setBusy(false)}
 }
 async function unbind(attachment:Attachment){
  setBusy(true);setMessage('');
  try{
   await api(`/tables/${tableId}/rows/${rowId}/attachments/${attachment.attachment_id}`,{method:'DELETE'});
   if(active.current!==key)return;await load();if(active.current!==key)return;await onChanged();if(active.current===key)setMessage('附件关联已解除');
  }catch(e){if(active.current===key)setMessage(String(e))}finally{if(active.current===key)setBusy(false)}
 }
 async function download(attachment:Attachment){
  setBusy(true);setMessage('');
  try{
   const response=await apiRaw(`/attachments/${attachment.attachment_id}/content?table_id=${encodeURIComponent(tableId)}`);
   const objectUrl=URL.createObjectURL(await response.blob()),link=document.createElement('a');
   link.href=objectUrl;link.download=attachment.filename;link.rel='noopener';document.body.append(link);link.click();link.remove();
   window.setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);
  }catch(e){if(active.current===key)setMessage(String(e))}finally{if(active.current===key)setBusy(false)}
 }
 return <section className="attachment-panel" aria-label="记录附件">
  <h4>附件</h4><p role="status" aria-live="polite">{message}</p>
  {canAttach&&<><label>上传附件（最多 16 MiB）<input type="file" disabled={busy} onChange={event=>{const file=event.currentTarget.files?.[0];if(file)void upload(file);event.currentTarget.value=''}}/></label>
   <form onSubmit={event=>{event.preventDefault();void bind()}}><label>关联此表中已有附件 ID<input value={sourceId} onChange={event=>setSourceId(event.target.value)} placeholder="附件 ID"/></label><button disabled={busy||!sourceId.trim()}>关联附件</button></form></>}
  {!items.length&&<p>当前记录没有附件。</p>}
  <ul>{items.map(item=><li key={item.attachment_id}><div><strong>{item.filename}</strong><small>{item.media_type} · {item.size.toLocaleString()} 字节 · {new Date(item.created_at).toLocaleString()}</small><small>{item.attachment_id}</small></div><button disabled={busy} onClick={()=>void download(item)}>鉴权下载</button>{canAttach&&<button disabled={busy} onClick={()=>void unbind(item)}>解除关联</button>}</li>)}</ul>
 </section>
}
