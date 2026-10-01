import React,{useEffect,useRef,useState} from 'react';
import {isActiveWork, isExpiredWorkClaim} from './work-state';
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
