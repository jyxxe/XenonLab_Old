import {Icon} from './design-system/Icon';
import React,{useEffect,useRef,useState} from 'react';
import {isActiveWork, isExpiredWorkClaim, workDisplayStatus} from './work-state';
import {forgejoIssueUrl} from './shell/forgejo-links.js';
import type {DesktopApp, PluginWidget} from './shell/types';
import {calendarMonth, calendarToday, shiftCalendarMonth} from './calendar.js';
type APIPage=(path:string,init?:RequestInit)=>Promise<{data:any;meta:{counts?:Record<string,number>;next_cursor?:string|null}}>;
type WorkItem={id:string;status:string;claim_active:boolean};
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

export function CalendarWidget({timezone}:{timezone:string}){
  const [now,setNow]=useState(()=>new Date());
  const [selectedMonth,setSelectedMonth]=useState<{year:number;month:number;timezone:string}|null>(null);
  useEffect(()=>{const timer=setInterval(()=>setNow(new Date()),60_000);return()=>clearInterval(timer)},[]);
  useEffect(()=>setSelectedMonth(null),[timezone]);
  const today=calendarToday(now,timezone);
  const displayed=selectedMonth?.timezone===timezone?selectedMonth:today;
  const {weekday,days,label:monthLabel}=calendarMonth(displayed);
  const currentMonth=displayed.year===today.year&&displayed.month===today.month;
  const browse=(offset:number)=>setSelectedMonth({...shiftCalendarMonth(displayed,offset),timezone});
  const headings=['日','一','二','三','四','五','六'];
  return <section className="widget widget-calendar" aria-label={`${monthLabel}日历`}>
    <header><strong role="status" aria-live="polite" aria-atomic="true">{monthLabel}</strong>
      <div className="calendar-controls">
        <button type="button" aria-label="上个月" onClick={()=>browse(-1)}>‹</button>
        <button type="button" aria-label="回到今天" onClick={()=>{setNow(new Date());setSelectedMonth(null)}}>今天</button>
        <button type="button" aria-label="下个月" onClick={()=>browse(1)}>›</button>
      </div>
    </header>
    <div className="calendar-grid" role="grid" aria-readonly="true" aria-label={`${monthLabel}日期`}>
      <div className="calendar-row" role="row">{headings.map((label,index)=><span className="calendar-weekday" role="columnheader" key={`${label}-${index}`}>{label}</span>)}</div>
      {Array.from({length:6},(_,week)=><div className="calendar-row" role="row" key={week}>{Array.from({length:7},(_,column)=>{
        const index=week*7+column;
        const value=index-weekday+1;
        const inMonth=value>=1&&value<=days;
        const isToday=currentMonth&&inMonth&&value===today.day;
        return <span role="gridcell" aria-label={inMonth?`${displayed.year}年${displayed.month}月${value}日`:undefined} aria-current={isToday?'date':undefined} className={isToday?'calendar-today':inMonth?'':'calendar-outside'} key={index}>{inMonth?value:''}</span>;
      })}</div>)}
    </div>
  </section>;
}

export function AppShortcutsWidget({apps,selected,onOpen}:{apps:DesktopApp[];selected:string[];onOpen:(id:string)=>void}){
  const choices=selected.length?apps.filter(app=>selected.includes(app.id)):apps.slice(0,4);
  return <section className="widget widget-shortcuts" aria-label="应用快捷入口">
    <header><strong>应用快捷入口</strong></header>
    {choices.length?<div className="shortcut-grid">{choices.slice(0,12).map(app=><button type="button" key={app.id} onClick={()=>onOpen(app.id)} aria-label={`打开${app.title}`}>
      <span aria-hidden="true"><Icon value={app.icon} size={24} /></span><small>{app.title}</small>
    </button>)}</div>:<p className="muted">没有可用的授权应用</p>}
  </section>;
}

type DetailedWorkItem={id:string;repository:string;issue_id:number;title:string;status:string;claim_active:boolean};
export function WorkWidget({apiPage,allowed,onOpen}:{apiPage:APIPage;allowed:boolean;onOpen:()=>void}){
  const [items,setItems]=useState<DetailedWorkItem[]|null>(null),[truncated,setTruncated]=useState(false);
  const latest=useRef(apiPage); latest.current=apiPage;
  useEffect(()=>{if(!allowed)return;let active=true;
    const load=async()=>{try{const page=await latest.current('/work?limit=1000');if(active){setItems(page.data);setTruncated(Boolean(page.meta.next_cursor))}}catch{if(active)setItems(null)}};
    void load();const timer=setInterval(load,60_000);return()=>{active=false;clearInterval(timer)};
  },[allowed]);
  const active=(items??[]).filter(isActiveWork).sort((a,b)=>String(a.repository??'').localeCompare(String(b.repository??''))||b.issue_id-a.issue_id).slice(0,5);
  return <section className="widget widget-work" aria-label="任务队列">
    <header><strong>任务队列</strong><i className={`status-dot${allowed&&items===null?' bad':''}`}/></header>
    {!allowed?<small>当前账户没有任务读取权限</small>:items===null?<small>加载失败，稍后重试</small>:active.length===0?<small>暂无进行中任务</small>:
      <ul>{active.map(item=>{
        const href=forgejoIssueUrl(item.repository,item.issue_id);
        const title=`${String(item.repository??'')} #${String(item.issue_id??'')} ${String(item.title??'')}`;
        return <li key={item.id}>{href?<a className="work-title" href={href} target="_blank" rel="noopener noreferrer" title={title}>{title}</a>:<span className="work-title">{title}</span>}<em data-status={workDisplayStatus(item)}>{workDisplayStatus(item)}</em></li>;
      })}</ul>}
    {allowed&&items!==null&&<small>{truncated?'至少 ':''}{items.filter(isActiveWork).length} 项进行中 · <button type="button" className="widget-link" onClick={onOpen}>打开任务</button></small>}
    {allowed&&items===null&&<small><button type="button" className="widget-link" onClick={onOpen}>打开任务面板</button></small>}
  </section>;
}

type NoteSaveState='saved'|'dirty'|'saving'|'error';
export function NotesWidget({notes,onSave}:{notes:string;onSave:(value:string,keepalive?:boolean)=>Promise<void>}){
  const [value,setValue]=useState(notes),[saveState,setSaveState]=useState<NoteSaveState>('saved');
  const latest=useRef(notes),synced=useRef(notes),timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
  const saveChain=useRef<Promise<void>>(Promise.resolve());
  const mounted=useRef(false);
  const saveLatest=useRef(onSave);saveLatest.current=onSave;
  const flushRef=useRef<(keepalive?:boolean,reportStatus?:boolean)=>Promise<void>>(async()=>{});
  flushRef.current=async(keepalive=false,reportStatus=true)=>{
    clearTimeout(timer.current);timer.current=undefined;
    const snapshot=latest.current;
    if(snapshot===synced.current)return;
    if(reportStatus&&mounted.current)setSaveState('saving');
    const pending=saveChain.current.catch(()=>undefined).then(()=>saveLatest.current(snapshot,keepalive));
    saveChain.current=pending;
    try{await pending;synced.current=snapshot;if(reportStatus&&mounted.current)setSaveState(latest.current===snapshot?'saved':'dirty');if(latest.current!==snapshot){if(mounted.current)timer.current=setTimeout(()=>void flushRef.current(),500);else void flushRef.current(true,false).catch(()=>undefined)}}
    catch{if(reportStatus&&mounted.current)setSaveState('error');throw new Error('便签尚未保存')}
  };
  useEffect(()=>{if(notes!==synced.current&&latest.current===synced.current){synced.current=notes;latest.current=notes;setValue(notes);setSaveState('saved')}},[notes]);
  useEffect(()=>{
    mounted.current=true;
    const onPageHide=()=>{void flushRef.current(true).catch(()=>undefined)};
    window.addEventListener('pagehide',onPageHide);
    return()=>{window.removeEventListener('pagehide',onPageHide);clearTimeout(timer.current);mounted.current=false;void flushRef.current(true,false).catch(()=>undefined)};
  },[]);
  const edit=(next:string)=>{latest.current=next;setValue(next);setSaveState('dirty');clearTimeout(timer.current);timer.current=setTimeout(()=>void flushRef.current().catch(()=>undefined),800)};
  return <section className="widget widget-notes" aria-label="快速便签">
    <header><strong>快速便签</strong><span role="status" aria-live="polite" data-save-state={saveState}>{saveState==='saved'?'已保存':saveState==='saving'?'保存中…':saveState==='error'?'保存失败，重新聚焦后重试':'有未保存修改'}</span></header>
    <textarea value={value} maxLength={4000} placeholder="随手记录，自动保存到账户偏好" aria-label="便签内容" onChange={event=>edit(event.target.value)} onBlur={()=>void flushRef.current().catch(()=>undefined)} />
    {value.length>3500&&<small>{value.length} / 4000</small>}
    <small>离开页面时会尽力同步；显示“已保存”后才确认服务端已确认保存。</small>
  </section>;
}

export function SearchWidget({onSearch,available}:{onSearch:(query:string)=>void;available:boolean}){
  const [query,setQuery]=useState('');
  const submit=()=>{const trimmed=query.trim();if(!trimmed||!available)return;onSearch(trimmed);setQuery('')};
  return <section className="widget widget-search" aria-label="知识库搜索">
    <header><strong>搜索知识库</strong></header>
    {!available?<small>当前账户没有可用的知识库应用</small>:<form onSubmit={event=>{event.preventDefault();submit()}}>
      <input value={query} maxLength={4096} placeholder="输入关键词" aria-label="知识库搜索关键词" onChange={event=>setQuery(event.target.value)} />
      <button type="submit" disabled={!query.trim()}>搜索</button>
    </form>}
  </section>;
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

/** Third-party dashboard widget. It is always a cross-origin sandboxed iframe:
 * the plugin never runs in the desktop origin and receives no session token. */
export function PluginWidget({widget}:{widget:PluginWidget}){
  let src='';
  try{src=new URL(widget.entry||'/',widget.origin).href}catch{src=''}
  if(!src)return <section className="widget widget-plugin" aria-label={`${widget.title}（配置无效）`}>
    <header><strong>{widget.title}</strong></header><small>组件入口无效，请在设置中重新安装插件。</small>
  </section>;
  return <section className="widget widget-plugin" aria-label={widget.title} style={{height:widget.height}}>
    <header><strong><Icon value={widget.icon} size={16} /> {widget.title}</strong>
      <button type="button" className="widget-link" onClick={()=>window.open(src,'_blank','noopener')} title="在浏览器中打开组件">↗</button>
    </header>
    <iframe title={widget.title} src={src} sandbox="allow-scripts allow-same-origin allow-forms" referrerPolicy="no-referrer" loading="lazy" />
  </section>;
}
