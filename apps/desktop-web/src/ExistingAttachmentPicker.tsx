import React, {useEffect, useRef, useState} from 'react';
import {Button, Input, Modal} from './design-system/primitives';

type Candidate = {attachment_id:string;row_id:string;filename:string;media_type:string;size:number;source_label:string};
type APIPage = (path:string)=>Promise<{data:any;meta:Record<string,any>}>;

/** Metadata discovery only; the existing bind endpoint remains the write authority. */
export function ExistingAttachmentPicker({tableId,rowId,apiPage,busy,bindError,onBind,onClose}:{
 tableId:string;rowId:string;apiPage:APIPage;busy:boolean;bindError:string;
 onBind:(id:string)=>Promise<boolean>;onClose:()=>void;
}) {
 const [draft,setDraft]=useState(''),[query,setQuery]=useState('');
 const [items,setItems]=useState<Candidate[]>([]),[cursor,setCursor]=useState<string|null>(null);
 const [loading,setLoading]=useState(true),[error,setError]=useState('');
 const generation=useRef(0),inFlight=useRef(false),failedCursor=useRef<string|null>(null);
 async function load(after:string|null=null,replace=false){
  if(inFlight.current)return;
  inFlight.current=true;setLoading(true);setError('');failedCursor.current=after;
  const current=generation.current;
  try{
   const params=new URLSearchParams({limit:'25',q:query});if(after)params.set('cursor',after);
   const page=await apiPage(`/tables/${encodeURIComponent(tableId)}/attachments?${params}`);
   if(current!==generation.current)return;
   setItems(old=>{const previous=replace?[]:old,seen=new Set(previous.map(item=>item.attachment_id));return [...previous,...(page.data as Candidate[]).filter(item=>!seen.has(item.attachment_id))]});
   setCursor(typeof page.meta.next_cursor==='string'&&page.meta.next_cursor?page.meta.next_cursor:null);
  }catch(cause){if(current===generation.current)setError(String(cause))}
  finally{if(current===generation.current){inFlight.current=false;setLoading(false)}}
 }
 useEffect(()=>{
  ++generation.current;inFlight.current=false;setItems([]);setCursor(null);void load(null,true);
  return()=>{++generation.current;inFlight.current=false};
 },[tableId,rowId,apiPage,query]);
 return <Modal open title="选择同表已有附件" onClose={onClose} size="lg">
  <p>只显示当前可读来源记录的文件元数据。选择后关联到当前记录。</p>
  <form onSubmit={event=>{event.preventDefault();if(!busy)setQuery(draft.trim())}}>
   <label>按文件名或来源记录搜索<Input type="search" maxLength={200} value={draft} disabled={busy} onChange={event=>setDraft(event.target.value)}/></label>
   <Button type="submit" disabled={busy}>搜索已有附件</Button>
   <Button type="button" disabled={busy||(!draft&&!query)} onClick={()=>{setDraft('');setQuery('')}}>清除搜索</Button>
  </form>
  <p role="status" aria-live="polite">已加载 {items.length} 项{cursor?'，还有候选可继续读取':loading?'，正在读取…':error?'，读取未完成':'，当前查询读取结束'}。</p>
  {loading&&<p role="status">正在读取同表附件元数据…</p>}
  {error&&<div><p role="alert">读取已有附件失败：{error}</p><Button disabled={loading||busy} onClick={()=>void load(failedCursor.current,items.length===0)}>重试读取已有附件</Button></div>}
  {bindError&&<p role="alert">关联失败：{bindError}</p>}
  {!loading&&!error&&!items.length&&<p>{cursor?'本页没有可读匹配项，可继续读取。':'没有匹配的可读附件。'}</p>}
  <ul className="existing-attachment-list">{items.map(item=><li key={item.attachment_id}>
   <div><strong>{item.filename}</strong><small>{item.media_type} · {item.size.toLocaleString()} 字节</small><small>来源记录：{item.source_label}</small><small className="mono">{item.row_id}</small></div>
   <Button disabled={busy||loading} aria-label={`选择 ${item.filename}（来源：${item.source_label}）`} onClick={async()=>{if(await onBind(item.attachment_id))onClose()}}>选择并关联</Button>
  </li>)}</ul>
  {cursor&&!error&&<Button disabled={loading||busy} onClick={()=>void load(cursor)}>加载更多已有附件</Button>}
 </Modal>;
}
