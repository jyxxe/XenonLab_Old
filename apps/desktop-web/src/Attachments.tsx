import React,{useCallback,useEffect,useMemo,useRef,useState,useSyncExternalStore} from 'react';
import {createUploadStore, type UploadSession, type UploadStore} from './upload-state.js';

type API=(path:string,init?:RequestInit)=>Promise<any>;
type APIPage=(path:string,init?:RequestInit)=>Promise<{data:any;meta:{[key:string]:any}}>
type APIRaw=(path:string,init?:RequestInit)=>Promise<Response>;
type Attachment={attachment_id:string;row_id:string;filename:string;media_type:string;size:number;created_at:string;metadata:Record<string,any>};

export function Attachments({tableId,rowId,api,apiPage,apiRaw,canAttach,onChanged,uploadStore}:{tableId:string;rowId:string;api:API;apiPage?:APIPage;apiRaw:APIRaw;canAttach:boolean;onChanged:()=>Promise<void>;uploadStore?:UploadStore}){
 const [items,setItems]=useState<Attachment[]>([]),[message,setMessage]=useState(''),[sourceId,setSourceId]=useState(''),[localBusy,setBusy]=useState(false);
 const localStore=useMemo(()=>createUploadStore(),[]),store=uploadStore||localStore;
 const uploadKey=JSON.stringify([tableId,rowId]);
 const subscribe=useCallback((notify:()=>void)=>store.subscribe(uploadKey,notify),[store,uploadKey]);
 const snapshot=useCallback(()=>store.get(uploadKey),[store,uploadKey]);
 const uploadState=useSyncExternalStore(subscribe,snapshot);
 const {resume,progress}=uploadState,busy=localBusy||uploadState.busy;
 useEffect(()=>()=>localStore.dispose(),[localStore]);
 const [listStatus,setListStatus]=useState<'loading'|'loaded'|'error'>('loading'),[listError,setListError]=useState('');
 const request=useRef(0),active=useRef('');
 const key=`${tableId}:${rowId}`;
 async function load(){
  const current=++request.current;
  if(store.alive&&active.current===key){setListStatus('loading');setListError('')}
  try{
   const result=apiPage?await apiPage(`/tables/${tableId}/rows/${rowId}/attachments`):{data:await api(`/tables/${tableId}/rows/${rowId}/attachments`)};
   if(store.alive&&active.current===key&&request.current===current){setItems(result.data);setMessage('');setListStatus('loaded')}
  }catch(e){if(store.alive&&active.current===key&&request.current===current){setListStatus('error');setListError(String(e))}}
 }
 useEffect(()=>{active.current=key;setItems([]);setMessage('');void load();return()=>{active.current='';++request.current}},[tableId,rowId,store]);
 const wasUploading=useRef(uploadState.busy);
 useEffect(()=>{
  const finished=wasUploading.current&&!uploadState.busy&&!uploadState.resume;
  wasUploading.current=uploadState.busy;
  if(finished&&uploadState.message&&store.alive){
   const completedState=uploadState;
   const current=()=>store.alive&&active.current===key&&store.get(uploadKey)===completedState;
   void (async()=>{await load();if(current()){try{await onChanged()}catch(e){if(current())store.set(uploadKey,{message:`附件操作已完成，但刷新视图失败：${String(e)}`})}}})();
  }
 },[uploadState.busy,uploadState.resume]);
 async function transferLarge(file:File, session?:UploadSession){
  let started:UploadSession;
  if(session)started=session;
  else{
   const result=await api('/uploads',{method:'POST',body:JSON.stringify({table_id:tableId,row_id:rowId,filename:file.name,size:file.size})});
   started={uploadId:result.upload_id,nextPart:1,partCount:result.part_count,partSize:result.part_size};
  }
  if(!store.alive)return;
  store.set(uploadKey,{resume:{file,...started},progress:Math.floor(((started.nextPart-1)/started.partCount)*100)});
  for(let part=started.nextPart;part<=started.partCount;part++){
   const start=(part-1)*started.partSize,end=Math.min(file.size,start+started.partSize);
   if(!store.alive)return;
   let attempt=0;
   while(true){
    try{
     if(!store.alive)return;
     await apiRaw(`/uploads/${started.uploadId}/parts/${part}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:file.slice(start,end)});
     break;
    }catch(error){if(++attempt>=3)throw error}
   }
   if(!store.alive)return;
   const nextPart=part+1;store.set(uploadKey,{resume:{file,...started,nextPart},progress:Math.floor((part/started.partCount)*100)});
  }
  if(!store.alive)return;
  await api(`/uploads/${started.uploadId}/complete`,{method:'POST'});
  store.set(uploadKey,{resume:null,message:'附件已上传并关联到当前记录'});
 }
 async function upload(file:File, retry?:UploadSession){
  const form=new FormData();form.append('upload',file);
  if(!store.alive||store.get(uploadKey).busy)return;
  let uploaded=false;store.set(uploadKey,{busy:true,message:'',progress:file.size>16*1024*1024?0:null});setMessage('');
  try{
   if(file.size>16*1024*1024){await transferLarge(file,retry)}else{
    const response=await apiRaw(`/attachments?table_id=${encodeURIComponent(tableId)}&row_id=${encodeURIComponent(rowId)}`,{method:'POST',body:form});
    await response.json();
   }
   if(!store.alive)return;
   uploaded=true;
   store.set(uploadKey,{progress:null});
   if(file.size>16*1024*1024)return;
   if(!store.alive||active.current!==key)return;await load();if(!store.alive||active.current!==key)return;await onChanged();if(store.alive&&active.current===key)setMessage('附件已上传并关联到当前记录');
  }catch(e){const text=uploaded?`附件已上传，但刷新视图失败：${String(e)}`:`${String(e)}${store.get(uploadKey).resume?'；可点击“继续重试”续传':'；重新选择文件可重试'}`;store.set(uploadKey,{message:text});if(store.alive&&active.current===key)setMessage(text)}finally{store.set(uploadKey,{busy:false})}
 }
 async function cancelUpload(){
  const current=store.get(uploadKey);if(!store.alive||!current.resume||current.busy)return;store.set(uploadKey,{busy:true,message:''});setMessage('');
  try{await api(`/uploads/${current.resume.uploadId}`,{method:'DELETE'});store.set(uploadKey,{resume:null,progress:null,message:'分片上传已取消'});if(store.alive&&active.current===key)setMessage('分片上传已取消')}
  catch(e){store.set(uploadKey,{message:String(e)});if(store.alive&&active.current===key)setMessage(String(e))}finally{store.set(uploadKey,{busy:false})}
 }
 function clearCompletedMessage(){if(!store.get(uploadKey).resume&&!store.get(uploadKey).busy)store.set(uploadKey,{message:''})}
 async function bind(){
  if(!sourceId.trim())return;clearCompletedMessage();setBusy(true);setMessage('');
  try{
   await api(`/tables/${tableId}/rows/${rowId}/attachments`,{method:'POST',body:JSON.stringify({attachment_id:sourceId.trim()})});
   if(!store.alive||active.current!==key)return;setSourceId('');await load();if(!store.alive||active.current!==key)return;await onChanged();if(store.alive&&active.current===key)setMessage('附件已关联到当前记录');
  }catch(e){if(store.alive&&active.current===key)setMessage(String(e))}finally{if(store.alive&&active.current===key)setBusy(false)}
 }
 async function unbind(attachment:Attachment){
  clearCompletedMessage();setBusy(true);setMessage('');
  try{
   await api(`/tables/${tableId}/rows/${rowId}/attachments/${attachment.attachment_id}`,{method:'DELETE'});
   if(!store.alive||active.current!==key)return;await load();if(!store.alive||active.current!==key)return;await onChanged();if(store.alive&&active.current===key)setMessage('附件关联已解除');
  }catch(e){if(store.alive&&active.current===key)setMessage(String(e))}finally{if(store.alive&&active.current===key)setBusy(false)}
 }
 async function download(attachment:Attachment){
  clearCompletedMessage();setBusy(true);setMessage('');
  try{
   const response=await apiRaw(`/attachments/${attachment.attachment_id}/content?table_id=${encodeURIComponent(tableId)}`);
   const objectUrl=URL.createObjectURL(await response.blob()),link=document.createElement('a');
   link.href=objectUrl;link.download=attachment.filename;link.rel='noopener';document.body.append(link);link.click();link.remove();
   window.setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);
  }catch(e){if(store.alive&&active.current===key)setMessage(String(e))}finally{if(store.alive&&active.current===key)setBusy(false)}
 }
 return <section className="attachment-panel" aria-label="记录附件">
  <h4>附件</h4><p role="status" aria-live="polite">{message||uploadState.message}</p>
  {progress!==null&&<label>分片上传进度 <progress max="100" value={progress}>{progress}%</progress> {progress}%</label>}
  {canAttach&&<><label>上传附件（大于 16 MiB 时分片，最大 2 GiB）<input type="file" disabled={busy||Boolean(resume)} onChange={event=>{const file=event.currentTarget.files?.[0];if(file){void upload(file)}event.currentTarget.value=''}}/></label>
   {resume&&<><p role="status">当前分片会话未完成（{resume.file.name}）。请继续重试或取消后再选择其他文件。</p><button disabled={busy} onClick={()=>void upload(resume.file,resume)}>继续重试分片上传</button><button disabled={busy} onClick={()=>void cancelUpload()}>取消分片上传</button></>}
   <form onSubmit={event=>{event.preventDefault();void bind()}}><label>关联此表中已有附件 ID<input value={sourceId} onChange={event=>setSourceId(event.target.value)} placeholder="附件 ID"/></label><button disabled={busy||!sourceId.trim()}>关联附件</button></form></>}
  {listStatus==='loading'&&<p role="status">正在读取附件列表…</p>}
  {listStatus==='error'&&<div><p role="alert">读取附件列表失败：{listError}</p><button disabled={busy} onClick={()=>void load()}>重试读取附件</button></div>}
  {listStatus==='loaded'&&!items.length&&<p>当前记录没有附件。</p>}
  <ul>{items.map(item=><li key={item.attachment_id}><div><strong>{item.filename}</strong><small>{item.media_type} · {item.size.toLocaleString()} 字节 · {new Date(item.created_at).toLocaleString()}</small><small>{item.attachment_id}</small></div><button disabled={busy} onClick={()=>void download(item)}>鉴权下载</button>{canAttach&&<button disabled={busy} onClick={()=>void unbind(item)}>解除关联</button>}</li>)}</ul>
 </section>
}
