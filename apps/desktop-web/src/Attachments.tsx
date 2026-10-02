import React,{useEffect,useRef,useState} from 'react';

type API=(path:string,init?:RequestInit)=>Promise<any>;
type APIPage=(path:string,init?:RequestInit)=>Promise<{data:any;meta:{[key:string]:any}}>
type APIRaw=(path:string,init?:RequestInit)=>Promise<Response>;
type UploadSession={uploadId:string;nextPart:number;partCount:number;partSize:number};
type Attachment={attachment_id:string;row_id:string;filename:string;media_type:string;size:number;created_at:string;metadata:Record<string,any>};

export function Attachments({tableId,rowId,api,apiPage,apiRaw,canAttach,onChanged}:{tableId:string;rowId:string;api:API;apiPage?:APIPage;apiRaw:APIRaw;canAttach:boolean;onChanged:()=>Promise<void>}){
 const [items,setItems]=useState<Attachment[]>([]),[message,setMessage]=useState(''),[sourceId,setSourceId]=useState(''),[busy,setBusy]=useState(false),[progress,setProgress]=useState<number|null>(null);
 const [resume,setResume]=useState<{file:File;uploadId:string;nextPart:number;partCount:number;partSize:number}|null>(null);
 const [listStatus,setListStatus]=useState<'loading'|'loaded'|'error'>('loading'),[listError,setListError]=useState('');
 const request=useRef(0),active=useRef('');
 const key=`${tableId}:${rowId}`;
 async function load(){
  const current=++request.current;
  if(active.current===key){setListStatus('loading');setListError('')}
  try{
   const result=apiPage?await apiPage(`/tables/${tableId}/rows/${rowId}/attachments`):{data:await api(`/tables/${tableId}/rows/${rowId}/attachments`)};
   if(active.current===key&&request.current===current){setItems(result.data);setMessage('');setListStatus('loaded')}
  }catch(e){if(active.current===key&&request.current===current){setListStatus('error');setListError(String(e))}}
 }
 useEffect(()=>{active.current=key;void load();return()=>{active.current='';++request.current}},[tableId,rowId]);
 async function transferLarge(file:File, session?:UploadSession){
  let started:UploadSession;
  if(session)started=session;
  else{
   const result=await api('/uploads',{method:'POST',body:JSON.stringify({table_id:tableId,row_id:rowId,filename:file.name,size:file.size})});
   started={uploadId:result.upload_id,nextPart:1,partCount:result.part_count,partSize:result.part_size};
  }
  setResume({file,...started});
  setProgress(Math.floor(((started.nextPart-1)/started.partCount)*100));
  for(let part=started.nextPart;part<=started.partCount;part++){
   const start=(part-1)*started.partSize,end=Math.min(file.size,start+started.partSize);
   let attempt=0;
   while(true){
    try{
     await apiRaw(`/uploads/${started.uploadId}/parts/${part}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:file.slice(start,end)});
     break;
    }catch(error){if(++attempt>=3)throw error}
   }
   const nextPart=part+1;setResume({file,...started,nextPart});setProgress(Math.floor((part/started.partCount)*100));
  }
  await api(`/uploads/${started.uploadId}/complete`,{method:'POST'});
  setResume(null);
 }
 async function upload(file:File, retry?:UploadSession){
  const form=new FormData();form.append('upload',file);
  let uploaded=false;setBusy(true);setMessage('');setProgress(file.size>16*1024*1024?0:null);
  try{
   if(file.size>16*1024*1024){await transferLarge(file,retry)}else{
    const response=await apiRaw(`/attachments?table_id=${encodeURIComponent(tableId)}&row_id=${encodeURIComponent(rowId)}`,{method:'POST',body:form});
    await response.json();
   }
   uploaded=true;
   setProgress(null);
   if(active.current!==key)return;await load();if(active.current!==key)return;await onChanged();if(active.current===key)setMessage('附件已上传并关联到当前记录');
  }catch(e){if(active.current===key)setMessage(uploaded?`附件已上传，但刷新视图失败：${String(e)}`:`${String(e)}${file.size>16*1024*1024?'；可点击“继续重试”续传':'；重新选择文件可重试'}`)}finally{if(active.current===key)setBusy(false)}
 }
 async function cancelUpload(){
  if(!resume)return;setBusy(true);setMessage('');
  try{await api(`/uploads/${resume.uploadId}`,{method:'DELETE'});setResume(null);setProgress(null);setMessage('分片上传已取消')}
  catch(e){if(active.current===key)setMessage(String(e))}finally{if(active.current===key)setBusy(false)}
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
  {progress!==null&&<label>分片上传进度 <progress max="100" value={progress}>{progress}%</progress> {progress}%</label>}
  {canAttach&&<><label>上传附件（大于 16 MiB 时分片，最大 2 GiB）<input type="file" disabled={busy||Boolean(resume)} onChange={event=>{const file=event.currentTarget.files?.[0];if(file){setResume(null);void upload(file)}event.currentTarget.value=''}}/></label>
   {resume&&<><p role="status">当前分片会话未完成。请继续重试或取消后再选择其他文件。</p><button disabled={busy} onClick={()=>void upload(resume.file,resume)}>继续重试分片上传</button><button disabled={busy} onClick={()=>void cancelUpload()}>取消分片上传</button></>}
   <form onSubmit={event=>{event.preventDefault();void bind()}}><label>关联此表中已有附件 ID<input value={sourceId} onChange={event=>setSourceId(event.target.value)} placeholder="附件 ID"/></label><button disabled={busy||!sourceId.trim()}>关联附件</button></form></>}
  {listStatus==='loading'&&<p role="status">正在读取附件列表…</p>}
  {listStatus==='error'&&<div><p role="alert">读取附件列表失败：{listError}</p><button disabled={busy} onClick={()=>void load()}>重试读取附件</button></div>}
  {listStatus==='loaded'&&!items.length&&<p>当前记录没有附件。</p>}
  <ul>{items.map(item=><li key={item.attachment_id}><div><strong>{item.filename}</strong><small>{item.media_type} · {item.size.toLocaleString()} 字节 · {new Date(item.created_at).toLocaleString()}</small><small>{item.attachment_id}</small></div><button disabled={busy} onClick={()=>void download(item)}>鉴权下载</button>{canAttach&&<button disabled={busy} onClick={()=>void unbind(item)}>解除关联</button>}</li>)}</ul>
 </section>
}
