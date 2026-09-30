import React,{useEffect,useRef,useState} from 'react';
type API=(path:string,init?:RequestInit)=>Promise<any>;
type WorkItem={id:string;status:string};
type Job={job_id:string;status:string};
const zone=(timezone:string)=>timezone==='system'?undefined:timezone;
export function MenuClock({timezone}:{timezone:string}){
  const [now,setNow]=useState(()=>new Date());
  useEffect(()=>{const timer=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(timer)},[]);
  let time='',date='';
  try{time=now.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:zone(timezone)});date=now.toLocaleDateString('zh-CN',{weekday:'short',month:'long',day:'numeric',timeZone:zone(timezone)})}
  catch{time=now.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});date=now.toLocaleDateString('zh-CN',{weekday:'short',month:'long',day:'numeric'})}
  return <time className="menu-clock" title={timezone==='system'?'浏览器时区':timezone}>{date} {time}</time>;
}
export function ClockWidget({timezone}:{timezone:string}){
  const [now,setNow]=useState(()=>new Date());
  useEffect(()=>{const timer=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(timer)},[]);
  let time='',date='';
  try{time=now.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:zone(timezone)});date=now.toLocaleDateString('zh-CN',{weekday:'long',year:'numeric',month:'long',day:'numeric',timeZone:zone(timezone)})}
  catch{time=now.toLocaleTimeString('zh-CN');date=now.toLocaleDateString('zh-CN')}
  return <section className="widget widget-clock" aria-label="时钟"><b>{time}</b><span>{date}</span></section>;
}
export function StatusWidget({api,onOpen}:{api:API;onOpen:()=>void}){
  const [work,setWork]=useState<WorkItem[]|null>(null),[jobs,setJobs]=useState<Job[]|null>(null);
  const latest=useRef(api);
  latest.current=api;
  useEffect(()=>{let active=true;
    const load=async()=>{try{const data=await latest.current('/work?limit=100');if(active)setWork(data)}catch{if(active)setWork([])}
      try{const data=await latest.current('/jobs?limit=50');if(active)setJobs(data)}catch{if(active)setJobs(null)}};
    load();const timer=setInterval(load,60000);return()=>{active=false;clearInterval(timer)};
  },[]);
  const active_=work?.filter(item=>item.status==='queued'||item.status==='claimed').length??0;
  const running=jobs?jobs.filter(job=>job.status==='queued'||job.status==='running').length:null;
  return <button className="widget widget-status" onClick={onOpen} title="打开系统面板">
    <header><strong>系统状态</strong><i className={`status-dot${work===null?' bad':''}`}/></header>
    <dl><div><dt>Harness 任务</dt><dd>{work===null?'—':`${active_} 进行中`}</dd></div><div><dt>Embedding 队列</dt><dd>{running===null?'需管理员':`${running} 进行中`}</dd></div></dl>
    <small>点击打开系统面板 →</small>
  </button>;
}
