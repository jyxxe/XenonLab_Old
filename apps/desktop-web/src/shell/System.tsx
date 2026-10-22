import React, {useEffect, useId, useRef, useState} from 'react';
import {Badge, Button, EmptyState, ErrorState, Input, Skeleton} from '../design-system/primitives';
import {Dashboard} from '../Dashboard';
import type {AppViewProps} from './types';
import {workDisplayStatus} from '../work-state';
import {auditDisplayFields, filterAuditRows} from './audit-filter.js';
import {copyLanEntry, lanEntryState} from '../lan-entry.js';
import {AgentPrompts} from '../AgentPrompts';

const sections = [
  ['overview', '概览'], ['services', '服务'], ['storage', '存储'], ['embedding', 'Embedding'],
  ['hfcache', 'HF Cache'], ['agents', 'Agents'], ['deployments', '部署'], ['network', '网络'], ['audit', '审计'],
  ['prompts', '提示词'],
];
const statusLabels: Record<string,string> = {queued:'排队',running:'运行中',claimed:'已领取',expired:'领取已过期 · 可重领',completed:'已完成',failed:'失败',blocked:'阻塞'};
const watermarkLabels: Record<string,string> = {normal:'正常',evict_low_priority:'建议淘汰低优先级',stop_prefetch:'停止预取',retain_protected:'仅保留受保护项'};
const watermarkTone = (level?:string) => level==='normal' ? 'success' : level==='retain_protected' ? 'danger' : 'warning';
function formatBytes(bytes:number){
  if(!Number.isFinite(bytes)||bytes<0)return '—';
  const units=['B','KiB','MiB','GiB','TiB'];let value=bytes,index=0;
  while(value>=1024&&index<units.length-1){value/=1024;index++}
  return `${index>0&&value<100?value.toFixed(2):Math.round(value)} ${units[index]}`;
}
const unavailable: Record<string,[string,string]> = {
  hfcache:['HF Cache 尚未接入桌面','HF Cache 服务使用独立 API；当前桌面没有受控的状态读取入口。'],
  deployments:['部署状态尚未接入桌面','当前桌面没有发布历史 API。部署完成状态由发布协调器确认。'],
};

/** Read-only views use existing same-origin API boundaries; unavailable services are explicit. */
export function System(props: AppViewProps) {
  const {api,apiPage,openApp} = props;
  const [tab,setTab] = useState('overview');
  const [tick,setTick] = useState(0);
  const [loading,setLoading] = useState(false);
  const [error,setError] = useState('');
  const [rows,setRows] = useState<any[]>([]);
  const [cursor,setCursor] = useState<string|null>(null);
  const [loadingMore,setLoadingMore] = useState(false);
  const [ready,setReady] = useState<{service:string;ready:boolean}|null>(null);
  const [storage,setStorage] = useState<any|null>(null);
  const [storageUnavailable,setStorageUnavailable] = useState(false);
  const generation=useRef(0);
  const [online,setOnline] = useState(navigator.onLine);
  const [lanMessage,setLanMessage] = useState('');
  const [auditQuery,setAuditQuery] = useState('');
  const auditFilterId = useId();
  const auditInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const change = () => setOnline(navigator.onLine);
    addEventListener('online',change); addEventListener('offline',change);
    return () => {removeEventListener('online',change);removeEventListener('offline',change)};
  },[]);

  useEffect(() => {
    let active = true;
    generation.current += 1;
    setError('');setRows([]);setCursor(null);setReady(null);setLoadingMore(false);setStorage(null);setStorageUnavailable(false);
    const path = tab === 'embedding' ? '/jobs?limit=50' : tab === 'agents' ? '/work?limit=100' : tab === 'audit' ? '/audit?limit=100' : '';
    if (!path && tab !== 'services' && tab !== 'storage') {setLoading(false);return () => {generation.current += 1}}
    setLoading(true);
    (async () => {
      try {
        if (tab === 'services') {
          const response = await fetch('/health/ready');
          const body = await response.json();
          if (active) setReady({service:body.service || 'desktop-web',ready:response.ok});
        } else if (tab === 'storage') {
          const me = await api('/users/me');
          if (!me.scopes?.includes('hf-cache:read')) throw new Error('当前身份需要 hf-cache:read 权限才能查看缓存池水位。');
          try {
            const status = await api('/cache/storage');
            if (active) setStorage(status);
          } catch (cause) {
            const error = cause as {code?:string; status?:number} | null;
            // Until the HF-cache service has a production route (#66), the desktop
            // origin answers 404 from the API catch-all; an upstream-down proxy
            // answers 502/503. Both mean "telemetry not available", never cached data.
            if (error?.code === 'STORAGE_TELEMETRY_UNAVAILABLE' || [404, 502, 503].includes(Number(error?.status)) || /telemetry unavailable/i.test(String(cause))) {
              if (active) setStorageUnavailable(true);
              return;
            }
            throw cause;
          }
        } else {
          const me = await api('/users/me');
          const required = tab === 'agents' ? 'dev:read' : 'xelibrary:admin';
          if (!me.scopes?.includes(required)) throw new Error(`当前身份需要 ${required} 权限才能查看此页面。`);
          const page = await apiPage(path);
          if (active) {setRows(page.data);setCursor(page.meta.next_cursor || null)}
        }
      } catch (cause) {if (active) setError(String(cause))}
      finally {if (active) setLoading(false)}
    })();
    return () => {active = false;generation.current += 1};
  },[tab,tick,api,apiPage]);

  const title = sections.find(section => section[0] === tab)?.[1];
  const jobView = tab === 'embedding';
  const rowsView = ['embedding','agents','audit'].includes(tab);
  const missing = unavailable[tab];
  const visibleRows = tab === 'audit' ? filterAuditRows(rows,auditQuery) : rows;

  return <div className="system-app">
    <nav className="system-sidebar" aria-label="系统分类">
      <h2>系统状态</h2>
      {sections.map(([id,label]) => <button type="button" key={id} aria-current={tab===id ? 'page' : undefined} onClick={() => setTab(id)}>{label}</button>)}
    </nav>
    <section className="system-content" aria-label={`系统${title}`}>
      {tab === 'overview' ? <Dashboard apiPage={apiPage} openApp={openApp} canOpenTasks={props.apps.some(app=>app.id==='issues')}/> : tab === 'prompts' ? <AgentPrompts apiPage={props.apiPage}/> : <>
        <header className="app-head"><div><h2>{title}</h2><p>只展示当前可读取的真实状态</p></div><Button disabled={loading} onClick={() => setTick(value => value+1)}>刷新</Button></header>
        {loading ? <div role="status"><Skeleton height={28}/><Skeleton height={28}/><Skeleton height={28}/></div> : error ? <ErrorState title="状态读取失败" message={error} onRetry={() => setTick(value => value+1)}/> : missing ? <EmptyState title={missing[0]} description={missing[1]}/> : tab==='services' ? <>
          {ready && <p><Badge tone={ready.ready ? 'success' : 'danger'}>{ready.ready ? 'Ready' : 'Unavailable'}</Badge> {ready.service}</p>}
          <p className="muted">此探测只检查桌面服务。XeLibrary、Embedding 和外部应用的健康状态没有统一监控 API，不能据此认为所有服务在线。</p>
        </> : tab==='network' ? (() => {
          const lan = lanEntryState(location.origin, import.meta.env?.VITE_LAN_ORIGIN);
          return <dl className="dev-meta">
            <div><dt>浏览器连接提示</dt><dd>{online ? '浏览器报告网络可用' : '浏览器报告离线'}</dd></div>
            <div><dt>当前入口</dt><dd>{location.origin}</dd></div>
            <div className="lan-entry"><dt>内网直连入口</dt><dd>
              <code data-testid="lan-entry-origin">{lan.origin}</code>
              <Button size="sm" onClick={() => { void copyLanEntry(lan.origin).then(ok => setLanMessage(ok ? '已复制内网入口地址' : '复制失败：浏览器未提供剪贴板权限，请手动复制')) }}>复制内网入口</Button>
              <p className="muted" role="status">{lanMessage || lan.hint}</p>
            </dd></div>
            <div><dt>服务端网络与 Tunnel</dt><dd>状态读取尚未接入；浏览器提示不能确认 Tunnel 或上游服务健康。</dd></div>
          </dl>;
        })() : rowsView ? <>
          {jobView && <p className="muted">索引任务队列不等于模型健康检查；Qwen quality worker 的部署状态没有在此 API 中提供。</p>}
          {tab==='audit' && <>
            <div className="system-audit-filter">
              <label htmlFor={auditFilterId}>筛选已加载审计记录</label>
              <Input id={auditFilterId} ref={auditInput} type="search" value={auditQuery} placeholder="时间、事件、操作者或对象" onChange={event=>setAuditQuery(event.target.value)}/>
              <Button disabled={!auditQuery} onClick={()=>{setAuditQuery('');auditInput.current?.focus()}}>清除筛选</Button>
            </div>
            <p className="muted" role="status">匹配 {visibleRows.length} 条 / 已加载 {rows.length} 条。仅筛选已加载记录，加载更多后更新结果。</p>
          </>}
          {visibleRows.length ? <div className="system-table-scroll"><table className="ds-table"><thead><tr>{tab==='audit' ? <><th>时间</th><th>事件</th><th>操作者</th><th>对象</th></> : <><th>{jobView ? '任务 / 知识表' : 'Issue'}</th><th>状态</th><th>{jobView ? '更新时间' : '持有者'}</th><th>{jobView ? '错误' : '租约'}</th></>}</tr></thead><tbody>
            {visibleRows.map(row => {const displayStatus=jobView?row.status:workDisplayStatus(row);return <tr key={row.id || row.job_id}>{tab==='audit' ? <>{auditDisplayFields(row).map((value,index)=><td key={index} title={index===3 ? value : undefined}>{value}</td>)}</> : <><td title={jobView ? row.table_id : row.title}>{jobView ? `${row.job_id} · ${row.table_id}` : `#${row.issue_id} ${row.title}`}</td><td><Badge tone={displayStatus==='failed'||displayStatus==='blocked' ? 'danger' : displayStatus==='completed' ? 'success' : displayStatus==='expired' ? 'warning' : 'neutral'}>{statusLabels[displayStatus] || displayStatus}</Badge></td><td>{jobView ? row.updated_at || row.created_at || '—' : row.holder || '待领取'}</td><td>{jobView ? row.error || '—' : row.claim_active ? `有效 · epoch ${row.epoch}` : displayStatus==='expired' ? '已过期 · 可重领' : '无有效租约'}</td></>}</tr>})}
          </tbody></table></div> : <EmptyState title={tab==='audit' && rows.length ? "没有匹配的审计记录" : "暂无记录"} description={tab==='audit' && rows.length ? `请调整或清除筛选；筛选仅覆盖已加载记录。${cursor ? "也可加载更多。" : ""}` : "此 API 当前没有返回可见记录。"}/>}
          {cursor && <Button disabled={loadingMore} onClick={async () => {
            const currentGeneration=generation.current;
            setLoadingMore(true);
            try {
              const prefix=tab==='embedding'?'/jobs?limit=50':tab==='agents'?'/work?limit=100':'/audit?limit=100';
              const page=await apiPage(`${prefix}&cursor=${encodeURIComponent(cursor)}`);
              if (generation.current !== currentGeneration) return;
              setRows(old=>[...old,...page.data.filter((item:any)=>!old.some(previous=>(previous.id||previous.job_id)===(item.id||item.job_id)))]);
              setCursor(page.meta.next_cursor||null);
            } catch(cause) {if(generation.current===currentGeneration)setError(String(cause))}
            finally {if(generation.current===currentGeneration)setLoadingMore(false)}
          }}>{loadingMore ? '读取中…' : '加载更多'}</Button>}
        </> : tab==='storage' ? storage ? <>
          <dl className="dev-meta">
            <div><dt>共享物理池</dt><dd><Badge tone={watermarkTone(storage.watermark)}>{watermarkLabels[storage.watermark]||storage.watermark||'未知'}</Badge> {storage.pool||'未知池'} · 池用量 {storage.used_percent}%</dd></div>
            <div><dt>物理池用量</dt><dd>{formatBytes(storage.pool_used_bytes)} / {formatBytes(storage.pool_total_bytes)}</dd></div>
            <div><dt>HF-cache guest 文件系统</dt><dd>{formatBytes(storage.guest_used_bytes)} / {formatBytes(storage.guest_total_bytes)}</dd></div>
            <div><dt>HF-cache 缓存占用</dt><dd>{formatBytes(storage.cached_bytes)}</dd></div>
            <div><dt>采样时间</dt><dd>{storage.sampled_at ? new Date(storage.sampled_at).toLocaleString() : '—'}</dd></div>
          </dl>
          <p className="muted">物理池与 guest 文件系统是同一共享池的两个视图，不能相加；空余空间取两者较小值。上游遥测缺失或过期时此处保持不可用，不展示缓存数字。</p>
        </> : <EmptyState title="缓存池水位暂不可用" description="受信遥测缺失、过期或上游不可达；为避免误导，此处不展示任何缓存数字。可点击刷新重试。"/> : null}
      </>}
    </section>
  </div>;
}
