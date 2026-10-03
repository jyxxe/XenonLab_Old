import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {Bars,Legend,Meter,Ring,chartColor} from './MacCharts';
import {ErrorState} from './design-system/primitives';
import {isActiveWork, isExpiredWorkClaim, workDisplayStatus} from './work-state';
import {forgejoIssueUrl} from './shell/forgejo-links.js';
import {directoryDistribution} from './directory-distribution.js';
type APIPage=(path:string,init?:RequestInit)=>Promise<{data:any;meta:{request_id?:string;next_cursor?:string|null;[key:string]:any}}>;
type WorkItem={id:string;repository:string;issue_id:number;title:string;status:string;holder:string|null;claim_active:boolean};
type Job={job_id:string;owner:string;table_id:string;status:string;created_at?:string;updated_at?:string;error?:string};
type Table={id:string;name:string;path:string};
const jobOrder=['queued','running','completed','failed'] as const;
const jobLabels:Record<string,string>={queued:'排队',running:'运行中',completed:'完成',failed:'失败'};
const jobColors:Record<string,string>={queued:'#4e8cde',running:'#ff9f0a',completed:'#30d158',failed:'#ff453a'};
const workOrder=['queued','claimed','expired','completed','blocked'] as const;
const workLabels:Record<string,string>={queued:'排队',claimed:'进行中',expired:'已过期',completed:'已完成',blocked:'阻塞'};
function timeAgo(iso?:string){
  const stamp=iso?Date.parse(iso):NaN;
  if(Number.isNaN(stamp))return '—';
  const seconds=Math.max(0,(Date.now()-stamp)/1000);
  if(seconds<60)return `${Math.round(seconds)} 秒前`;
  if(seconds<3600)return `${Math.round(seconds/60)} 分钟前`;
  if(seconds<86400)return `${Math.round(seconds/3600)} 小时前`;
  return `${Math.round(seconds/86400)} 天前`;
}
function jobDuration(job:Job){
  if(job.status!=='completed')return '';
  const start=job.created_at?Date.parse(job.created_at):NaN, end=job.updated_at?Date.parse(job.updated_at):NaN;
  if(Number.isNaN(start)||Number.isNaN(end)||end<start)return '';
  return end-start<1000?`${end-start} ms`:`${((end-start)/1000).toFixed(1)} s`;
}
export function Dashboard({apiPage,openApp}:{apiPage:APIPage;openApp:(id:string)=>void}){
  const [work,setWork]=useState<WorkItem[]>([]),[workTruncated,setWorkTruncated]=useState(false),[jobs,setJobs]=useState<Job[]>([]),[tables,setTables]=useState<Table[]>([]),[tablesTruncated,setTablesTruncated]=useState(false),[tablesLoading,setTablesLoading]=useState(true),[jobCounts,setJobCounts]=useState<Record<string,number>|null>(null);
  const [notes,setNotes]=useState<Record<string,string>>({}),[health,setHealth]=useState<{service:string;ready:boolean;latency:number}|null>(null);
  const [stamp,setStamp]=useState(0),[tick,setTick]=useState(0);
  const gridRef=useRef<HTMLDivElement>(null);
  const retry=()=>{setTablesLoading(true);setTick(value=>value+1)};
  useEffect(()=>{
    let active=true;
    setTablesLoading(true);
    const optional=async(key:string,load:()=>Promise<void>)=>{try{await load();if(active)setNotes(old=>{const next={...old};delete next[key];return next})}catch(error){if(active)setNotes(old=>({...old,[key]:String(error)}))}};
    (async()=>{
      await Promise.all([
        optional('work',async()=>{try{const page=await apiPage('/work?limit=1000');if(active){setWork(page.data);setWorkTruncated(Boolean(page.meta.next_cursor))}}catch(error){throw Error(/403|scope required/.test(String(error))?'需要 dev:read 权限才能查看任务队列':String(error))}}),
        optional('jobs',async()=>{try{const page=await apiPage('/jobs?limit=50');if(active){setJobs(page.data);setJobCounts(page.meta.counts||null)}}catch(error){throw Error(/403|scope required/.test(String(error))?'需要 xelibrary:admin 权限才能查看索引队列':String(error))}}),
        optional('tables',async()=>{try{const page=await apiPage('/tables?limit=1000');if(active){setTables(page.data);setTablesTruncated(Boolean(page.meta.next_cursor))}}finally{if(active)setTablesLoading(false)}}),
        optional('health',async()=>{const started=performance.now();const ready=await fetch('/health/ready');const body=await ready.json();if(active)setHealth({service:body.service||'desktop-web',ready:ready.ok,latency:Math.round(performance.now()-started)})}),
      ]);
      if(active)setStamp(Date.now());
    })();
    const next=setTimeout(()=>{if(active){setTablesLoading(true);setTick(value=>value+1)}},30000);
    return ()=>{active=false;clearTimeout(next)};
  },[tick]);
  useLayoutEffect(()=>{
    const grid=gridRef.current;
    if(!grid)return;
    const cards=Array.from(grid.children) as HTMLElement[];
    const layout=()=>{
      const gap=parseFloat(getComputedStyle(grid).getPropertyValue('--dashboard-card-gap'))||0;
      // One-pixel grid tracks let each column pack independently, preserving DOM order.
      cards.forEach(card=>{
        const span=Math.ceil(card.getBoundingClientRect().height+gap);
        const rowEnd=`span ${Math.max(1,span)}`;
        if(card.style.gridRowEnd!==rowEnd)card.style.gridRowEnd=rowEnd;
      });
    };
    layout();
    const resize=new ResizeObserver(layout);
    resize.observe(grid);
    cards.forEach(card=>resize.observe(card,{box:'border-box'}));
    return()=>resize.disconnect();
  },[]);
  const tableName=(id:string)=>{const table=tables.find(item=>item.id===id);return table?table.path+'/'+table.name:id.slice(0,8)};
  const jobSegments=jobOrder.map(status=>({label:jobLabels[status],value:jobCounts?.[status]??jobs.filter(job=>job.status===status).length,color:jobColors[status]}));
  const workSegments=workOrder.map((status,index)=>({label:workLabels[status],value:status==='claimed'?work.filter(item=>item.status==='claimed'&&item.claim_active).length:status==='expired'?work.filter(isExpiredWorkClaim).length:work.filter(item=>item.status===status).length,color:chartColor(index)}));
  const visibleWork=[...work.filter(isExpiredWorkClaim),...work.filter(isActiveWork)];
  const pathCategories=directoryDistribution(tables);
  const pathData=pathCategories.map((category,index)=>({label:String(index+1),value:category.value,
    color:category.kind==='other'?'#8e8e93':chartColor(index),
    hint:category.kind==='other'?`其他（汇总）：${category.directories.length} 个目录，${category.value} 张表；完整目录：${category.directories.map(directory=>`${directory.name}（${directory.value} 张表）`).join('、')}`:`目录：${category.name}，${category.value} 张表`}));
  return <section className="dashboard">
    <header className="dashboard-head"><div><h2>系统面板</h2><p>Embedding 索引队列、Harness 任务与知识库概览</p></div>
      <div className="dashboard-actions"><small>{stamp?`更新于 ${new Date(stamp).toLocaleTimeString('zh-CN')}`:'加载中…'}</small><button onClick={retry}>刷新</button></div></header>
      <div className="dashboard-grid" ref={gridRef}>
      <article className="mac-card"><header><h3>我的 Embedding 索引</h3><span className="mac-badge">最近 {jobs.length} 条</span></header>
        {notes.jobs?<ErrorState title="无法读取索引队列" message={notes.jobs} onRetry={retry}/>:<>
          <Meter segments={jobSegments}/>
          <Legend items={jobSegments}/>
          {jobs.length>0&&<ul className="job-list">{jobs.slice(0,6).map(job=><li key={job.job_id}>
            <span className={`chip ${job.status}`}>{jobLabels[job.status]||job.status}</span>
            <span className="job-table" title={tableName(job.table_id)}>{tableName(job.table_id)}</span>
            <small>{timeAgo(job.created_at)}{jobDuration(job)?` · ${jobDuration(job)}`:''}</small>
          </li>)}</ul>}
          {!jobs.length&&<p className="muted">暂无索引任务。管理员可在知识库窗口中对当前表重建索引。</p>}
        </>}
      </article>
      <article className="mac-card"><header><h3>Harness 任务队列</h3><span className="mac-badge">{workTruncated?`${work.length}+ 项`:`共 ${work.length} 项`}</span></header>
        {notes.work?<ErrorState title="无法读取任务队列" message={notes.work} onRetry={retry}/>:<div className="card-split">
          <Ring segments={workSegments} center={{value: work.length, label: workTruncated?'已载入':'任务'}}/>
          <Legend items={workSegments}/>
        </div>}
        {!notes.work&&workTruncated&&<p className="muted">状态统计仅覆盖当前载入的前 1000 项。</p>}
        {!notes.work&&visibleWork.length>0&&<ul className="job-list">{visibleWork.slice(0,4).map(item=>{
          const href=forgejoIssueUrl(item.repository,item.issue_id);
          return <li key={item.id}>
          <span className={`chip ${workDisplayStatus(item)}`}>{workDisplayStatus(item)==='expired'?'已过期 · 可重领':workLabels[item.status]||item.status}</span>
          {href?<a className="job-table" href={href} target="_blank" rel="noopener noreferrer" title={item.title}>#{item.issue_id} {item.title}</a>:<span className="job-table" title={item.title}>#{item.issue_id} {item.title}</span>}
          <small>{item.holder||'待领取'}</small>
        </li>})}</ul>}
        <button onClick={()=>openApp('work')}>打开任务中心</button>
      </article>
      <article className="mac-card"><header><h3>知识库概览</h3><span className="mac-badge">{tablesLoading?'读取中…':notes.tables?'不可用':tablesTruncated?`${tables.length}+ 张表`:`${tables.length} 张表`}</span></header>
        {notes.tables?<ErrorState title="无法读取知识库概览" message={notes.tables} onRetry={retry}/>:tablesLoading?<p className="muted" role="status">正在读取可见知识表…</p>:pathData.length?<div className="directory-distribution"><Bars data={pathData} unit=" 张表"/><ol className="directory-legend" aria-label="首层目录分布">{pathCategories.map((category,index)=><li key={index} title={pathData[index].hint}>
          <b style={{borderBottomColor:pathData[index].color}} aria-hidden="true">{index+1}</b><span>{category.kind==='other'?`其他（${category.directories.length} 个目录汇总）`:`目录：${category.name}`}</span><em>{category.value} 张表</em>
        </li>)}</ol></div>:<p className="muted">暂无可见知识表。</p>}
        {!notes.tables&&!tablesLoading&&tablesTruncated&&<p className="muted">路径统计仅覆盖当前载入的前 1000 张表。</p>}
        <button onClick={()=>openApp('knowledge')}>打开知识库</button>
      </article>
      <article className="mac-card"><header><h3>服务状态</h3></header>
        {notes.health?<ErrorState title="无法读取服务状态" message={notes.health} onRetry={retry}/>:health&&<div className="check-list">
          <div className="status-line"><i className={`status-dot${health.ready?'':' bad'}`}/><span>{health.service}</span><em className="muted">{health.ready?'ready':'不可用'} · {health.latency} ms</em></div>
          <div className="status-line"><i className={`status-dot${notes.jobs?' bad':''}`}/><span>我的索引队列</span><em className="muted">{notes.jobs?'需要管理员权限查看':`${(jobCounts?.queued??jobs.filter(job=>job.status==='queued').length)+(jobCounts?.running??jobs.filter(job=>job.status==='running').length)} 项进行中`}</em></div>
        </div>}
      </article>
    </div>
  </section>;
}
