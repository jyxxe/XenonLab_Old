import React,{useEffect,useRef,useState} from 'react';
import {isActiveWork, isExpiredWorkClaim, workDisplayStatus} from './work-state';
import {monthMatrix, zonedToday} from './calendar.js';
type APIPage=(path:string,init?:RequestInit)=>Promise<{data:any;meta:{counts?:Record<string,number>;next_cursor?:string|null}}>;
type WorkItem={id:string;status:string;claim_active:boolean};
type DetailedWorkItem={id:string;repository:string;issue_id:number;title:string;status:string;claim_active:boolean};
type Job={job_id:string;status:string};
const zone=(timezone:string)=>timezone==='system'?undefined:timezone;
export function MenuClock({timezone}:{timezone:string}){
  const [now,setNow]=useState(()=>new Date());
  useEffect(()=>{const timer=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(timer)},[]);
  let time='',date='';
  try{time=now.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:zone(timezone)});date=now.toLocaleDateString('zh-CN',{weekday:'short',month:'long',day:'numeric',timeZone:zone(timezone)})}
  catch{time=now.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});date=now.toLocaleDateString('zh-CN',{weekday:'short',month:'long',day:'numeric'})}
  return <time className="menu-clock" title={timezone==='system'?'浏览器时区':timezone}><span className="clock-date">{date}</span> <span className="clock-time">{time}</span></time>;
}
export function ClockWidget({timezone}:{timezone:string}){
  const [now,setNow]=useState(()=>new Date());
  useEffect(()=>{const timer=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(timer)},[]);
  let time='',date='';
  try{time=now.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:zone(timezone)});date=now.toLocaleDateString('zh-CN',{weekday:'long',year:'numeric',month:'long',day:'numeric',timeZone:zone(timezone)})}
  catch{time=now.toLocaleTimeString('zh-CN');date=now.toLocaleDateString('zh-CN')}
  return <section className="widget widget-clock" aria-label="时钟"><b>{time}</b><span>{date}</span></section>;
}
export function StatusWidget({apiPage,onOpen}:{apiPage:APIPage;onOpen:()=>void}){
  const [work,setWork]=useState<WorkItem[]|null>(null),[workTruncated,setWorkTruncated]=useState(false),[jobCounts,setJobCounts]=useState<Record<string,number>|null>(null),[jobs,setJobs]=useState<Job[]|null>(null);
  const latest=useRef(apiPage);
  latest.current=apiPage;
  useEffect(()=>{let active=true;
    const load=async()=>{try{const page=await latest.current('/work?limit=1000');if(active){setWork(page.data);setWorkTruncated(Boolean(page.meta.next_cursor))}}catch{if(active){setWork(null);setWorkTruncated(false)}}
      try{const page=await latest.current('/jobs?limit=50');if(active){setJobs(page.data);setJobCounts(page.meta.counts||null)}}catch{if(active){setJobs(null);setJobCounts(null)}}};
    load();const timer=setInterval(load,60000);return()=>{active=false;clearInterval(timer)};
  },[]);
  const active_=work?.filter(isActiveWork).length??0;
  const expired_=work?.filter(isExpiredWorkClaim).length??0;
  const running=jobs?(jobCounts?(jobCounts.queued||0)+(jobCounts.running||0):jobs.filter(job=>job.status==='queued'||job.status==='running').length):null;
  return <button className="widget widget-status" onClick={onOpen} title="打开系统面板">
    <header><strong>系统状态</strong><i className={`status-dot${work===null?' bad':''}`}/></header>
    <dl><div><dt>Harness 任务</dt><dd>{work===null?'—':`${workTruncated?'至少 ':''}${active_} 进行中`}</dd></div><div><dt>Embedding 队列</dt><dd>{running===null?'需管理员':`${running} 进行中`}</dd></div></dl>
    {expired_>0&&<small>{workTruncated?'至少 ':''}{expired_} 项领取已过期，可在任务中心重领</small>}
    <small>点击打开系统面板 →</small>
  </button>;
}
const WEEKDAY_LABELS=['一','二','三','四','五','六','日'];
export function CalendarWidget({timezone}:{timezone:string}){
  const [now,setNow]=useState(()=>new Date());
  useEffect(()=>{const timer=setInterval(()=>setNow(new Date()),30000);return()=>clearInterval(timer)},[]);
  const today=zonedToday(timezone,now);
  const weeks=today?monthMatrix(today.year,today.month):[];
  if(!today) return null;
  return <section className="widget widget-calendar" aria-label="日历">
    <header><strong>{today.year}年{today.month+1}月</strong></header>
    <table aria-label={`${today.year}年${today.month+1}月日历`}>
      <thead><tr>{WEEKDAY_LABELS.map(label=><th key={label} scope="col" aria-label={`星期${label}`}>{label}</th>)}</tr></thead>
      <tbody>
        {weeks.map((week,row)=><tr key={row}>{week.map((cell,col)=>(
          <td key={col} className={cell.inMonth?undefined:'outside'} aria-current={cell.inMonth&&cell.day===today.day?'date':undefined}>
            {cell.day}
          </td>))}</tr>)}
      </tbody>
    </table>
  </section>;
}
export function WorkWidget({apiPage,onOpen}:{apiPage:APIPage;onOpen:()=>void}){
  const [items,setItems]=useState<DetailedWorkItem[]|null>(null),[truncated,setTruncated]=useState(false);
  const latest=useRef(apiPage);
  latest.current=apiPage;
  useEffect(()=>{let active=true;
    const load=async()=>{try{const page=await latest.current('/work?limit=1000');if(active){setItems(page.data);setTruncated(Boolean(page.meta.next_cursor))}}catch{if(active)setItems(null)}};
    load();const timer=setInterval(load,60000);return()=>{active=false;clearInterval(timer)};
  },[]);
  const active=(items??[]).filter(isActiveWork).sort((a,b)=>b.issue_id-a.issue_id).slice(0,5);
  return <button className="widget widget-work" onClick={onOpen} title="打开任务中心">
    <header><strong>任务队列</strong><i className={`status-dot${items===null?' bad':''}`}/></header>
    {items===null?<small>加载失败，稍后重试</small>:active.length===0?<small>暂无进行中任务</small>:(
      <ul>{active.map(item=>(
        <li key={item.id}>
          <span className="work-title">#{item.issue_id} {item.title}</span>
          <em data-status={workDisplayStatus(item)}>{workDisplayStatus(item)}</em>
        </li>))}</ul>
    )}
    <small>{items===null?'':`${truncated?'至少 ':''}${(items??[]).filter(isActiveWork).length} 项进行中 · 点击打开任务中心 →`}</small>
  </button>;
}
type NotesSaver=(value:string,init?:RequestInit)=>Promise<void>;
const NOTES_SAVE_STATUS:Record<'saving'|'saved'|'error',string>={saving:'保存中…',saved:'已保存',error:'保存失败，继续输入会重试'};
export function NotesWidget({notes,onChange}:{notes:string;onChange:NotesSaver}){
  const [value,setValue]=useState(notes);
  const [status,setStatus]=useState<'idle'|'saving'|'saved'|'error'>('idle');
  const synced=useRef(notes);
  const timer=useRef<ReturnType<typeof setTimeout>>(undefined);
  const saving=useRef(false);
  const pending=useRef<null | {value:string;keepalive?:boolean}>(null);
  useEffect(()=>{if(notes!==synced.current&&!saving.current){synced.current=notes;setValue(notes);}},[notes]);
  const flush=(keepalive?:boolean)=>{
    clearTimeout(timer.current);
    const next=pending.current;
    pending.current=null;
    if(next===null||next.value===synced.current)return;
    synced.current=next.value;
    saving.current=true;
    setStatus('saving');
    onChange(next.value,keepalive?{keepalive:true}:undefined)
      .then(()=>{saving.current=false;if(pending.current===null)setStatus('saved')})
      .catch(()=>{saving.current=false;setStatus('error')});
  };
  useEffect(()=>()=>{
    // Window teardown (close, logout, navigation): the last burst of typing is
    // flushed synchronously here; keepalive lets the PATCH survive navigation.
    flush(true);
    clearTimeout(timer.current);
  },[]);
  useEffect(()=>{
    const onPageHide=()=>flush(true);
    addEventListener('pagehide',onPageHide);
    return()=>removeEventListener('pagehide',onPageHide);
  },[]);
  const edit=(next:string)=>{
    setValue(next);
    pending.current={value:next};
    setStatus('idle');
    clearTimeout(timer.current);
    timer.current=setTimeout(()=>flush(),800);
  };
  return <section className="widget widget-notes" aria-label="快速便签">
    <header><strong>快速便签</strong>{status!=='idle'&&<small role="status" data-tone={status==='error'?'error':undefined}>{NOTES_SAVE_STATUS[status]}</small>}</header>
    <textarea
      value={value}
      maxLength={4000}
      placeholder="随手记录，自动保存到账户偏好"
      aria-label="便签内容"
      onChange={event=>edit(event.target.value)}
    />
    {value.length>3500&&<small>{value.length} / 4000</small>}
  </section>;
}
export function SearchWidget({onSearch}:{onSearch:(query:string)=>void}){
  const [query,setQuery]=useState('');
  const submit=()=>{const trimmed=query.trim();if(!trimmed)return;onSearch(trimmed);setQuery('');};
  return <section className="widget widget-search" aria-label="知识库搜索">
    <header><strong>搜索知识库</strong></header>
    <form onSubmit={event=>{event.preventDefault();submit();}}>
      <input value={query} placeholder="输入关键词" aria-label="知识库搜索关键词" onChange={event=>setQuery(event.target.value)} />
      <button type="submit" disabled={!query.trim()}>搜索</button>
    </form>
  </section>;
}
