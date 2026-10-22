import React,{useEffect,useRef,useState} from 'react';
import {isUnregisteredAccountError} from './api-error.js';
import {AccountAudit} from './AccountAudit';
import type {APIPage} from './shell/types';
import {PASSWORD_HINT,USERNAME_HINT,formatCreateAccountError,validateNewAccount} from './account-create.js';
type API=(path:string,init?:RequestInit)=>Promise<any>;
type Account={id:string;parent:string|null;username:string;name:string;enabled:boolean;inherit_all:boolean;grants:string[];effective_scopes:string[];kind:string};
const labels:Record<string,string>={'accounts:manage':'管理子账号','desktop:read':'访问桌面','desktop:admin':'管理桌面应用','dev:read':'读取开发任务','dev:write':'更新开发任务','xelibrary:read':'读取知识库','xelibrary:write':'编辑知识库','xelibrary:query':'查询知识库','xelibrary:attach':'管理附件','xelibrary:admin':'管理知识库权限','xelibrary:theorem:read':'读取定理','xelibrary:theorem:propose':'提交定理提案','xelibrary:theorem:promote':'晋升定理','embedding:read':'读取向量','embedding:write':'生成向量','llm:use':'使用模型','deploy:read':'查看发布','deploy:release':'登记发布','deploy:approve':'审批发布','deploy:execute':'执行发布（仍需 Codex 身份）'};
function displayError(error:unknown,fallback:string){return error instanceof Error&&error.message.trim()?error.message:fallback}
function Scopes({available,locked=[],selected,setSelected}:{available:string[];locked?:string[];selected:string[];setSelected:(s:string[])=>void}){return <div className="scope-grid">{available.map(scope=><label key={scope}><input type="checkbox" checked={selected.includes(scope)} onChange={e=>setSelected(e.target.checked?[...selected,scope]:selected.filter(s=>s!==scope))}/>{labels[scope]||scope}</label>)}{locked.map(scope=><label key={scope} title="该权限由登录角色持有，但根账号尚未采纳；请先用根账号登录并在账号页勾选采纳，之后才能授予子账号。"><input type="checkbox" disabled/>{labels[scope]||scope}<small>（根账号未采纳）</small></label>)}</div>}
export function Accounts({api,apiPage,authEpoch=0}:{api:API;apiPage:APIPage;authEpoch?:number}){
 const [me,setMe]=useState<any>(null),[nodes,setNodes]=useState<Account[]>([]),[chosen,setChosen]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const [rootCandidates,setRootCandidates]=useState<string[]>([]);
 const [treeFilter,setTreeFilter]=useState('');
 const [treeState,setTreeState]=useState<'loading'|'unregistered'|'ready'>('loading');
 const [name,setName]=useState(''),[enabled,setEnabled]=useState(true),[inherit,setInherit]=useState(false),[grants,setGrants]=useState<string[]>([]);
 const [parent,setParent]=useState(''),[username,setUsername]=useState(''),[newName,setNewName]=useState(''),[password,setPassword]=useState(''),[newAll,setNewAll]=useState(false),[newScopes,setNewScopes]=useState<string[]>(['desktop:read']);
 const [createErrors,setCreateErrors]=useState<Partial<Record<'username'|'name'|'password',string>>>({}),[createError,setCreateError]=useState('');
 const usernameInput=useRef<HTMLInputElement>(null),nameInput=useRef<HTMLInputElement>(null),passwordInput=useRef<HTMLInputElement>(null);
 const [reset,setReset]=useState(''),[keys,setKeys]=useState<any[]>([]),[keyName,setKeyName]=useState(''),[keyScopes,setKeyScopes]=useState<string[]>([]),[secret,setSecret]=useState('');
 const selected=nodes.find(n=>n.id===chosen),parentNode=nodes.find(n=>n.id===parent);
 const treeQuery=treeFilter.trim().toLowerCase();
 const visibleNodes=treeQuery?nodes.filter(node=>node.name.toLowerCase().includes(treeQuery)||node.username.toLowerCase().includes(treeQuery)):nodes;
 const auditAccount=(id:string)=>{const account=nodes.find(node=>node.id===id);return account?`${account.name}（${account.username}） · ${id}`:id};
 const canManage=me?.kind==='human'&&(me.scopes.includes('accounts:manage')||me.scopes.includes('desktop:admin'));
 const canPolicy=canManage&&!!selected&&(chosen!==me?.subject||selected.parent===null);
 async function load(preferredId?:string){const identity=await api('/users/me');setMe(identity);let rows:Account[]=[],cursor='';try{for(;;){const page=await api('/auth/accounts?limit=1000&cursor='+encodeURIComponent(cursor));rows.push(...page);if(page.length<1000)break;cursor=page[page.length-1].id}}catch(error){if(!isUnregisteredAccountError(error))throw error;setNodes([]);setChosen('');setParent('');setRootCandidates([]);setTreeState('unregistered');return}setNodes(rows);setTreeState('ready');setChosen(old=>preferredId&&rows.some(r=>r.id===preferredId)?preferredId:rows.some(r=>r.id===old)?old:identity.subject);setParent(old=>rows.some(r=>r.id===old)?old:identity.subject);setRootCandidates([]);const ownRoot=rows.find(row=>row.id===identity.subject&&row.parent===null);if(identity.kind==='human'&&ownRoot&&(identity.scopes.includes('accounts:manage')||identity.scopes.includes('desktop:admin'))){try{const result=await api('/auth/accounts/root-scope-candidates');setRootCandidates(result.candidate_scopes)}catch{/* Candidate scopes are optional; the server denies unsigned/API-key identities. */}}}
 async function act(action:()=>Promise<void>){setBusy(true);setMessage('');try{await action()}catch(e){setMessage(displayError(e,'操作失败，请重试'))}finally{setBusy(false)}}
 useEffect(()=>{act(load)},[]);
 useEffect(()=>{setKeyName('')},[chosen]);
 useEffect(()=>{
  if(!selected)return;
  let active=true;
  setName(selected.name);setEnabled(selected.enabled);setInherit(selected.inherit_all);setGrants(selected.grants);
  setKeyScopes(selected.effective_scopes.filter(s=>s==='desktop:read'&&me?.scopes?.includes(s)));
  setSecret('');setReset('');setKeys([]);
  api('/auth/api-keys?owner='+encodeURIComponent(chosen)).then(items=>{if(active)setKeys(items)}).catch(e=>{if(active)setMessage(displayError(e,'读取 API key 失败，请重试'))});
  return()=>{active=false};
 },[chosen,nodes]);
 const depth=(n:Account)=>{let d=0,p=n.parent;const seen=new Set<string>();while(p&&d<128&&!seen.has(p)){seen.add(p);const row=nodes.find(r=>r.id===p);if(!row)break;p=row.parent;d++}return d};
 const available=selected?.parent?nodes.find(n=>n.id===selected.parent)?.effective_scopes||selected.effective_scopes:selected?.effective_scopes||[];
 const isOwnRoot=!!selected&&selected.id===me?.subject&&selected.parent===null&&me?.kind==='human';
 const editableScopes=isOwnRoot?[...new Set([...available,...rootCandidates])].sort():available;
 const unavailableGrants=grants.filter(scope=>!editableScopes.includes(scope));
 async function saveAccount(){
  if(!selected)return;
  const patch:Record<string,unknown>={};
  if(name!==selected.name)patch.name=name;
  if(canPolicy){
   if(enabled!==selected.enabled)patch.enabled=enabled;
   if(inherit!==selected.inherit_all)patch.inherit_all=inherit;
   const before=[...new Set(selected.grants)].sort(),after=[...new Set(grants)].sort();
   if(before.join('\0')!==after.join('\0')){
    if(unavailableGrants.length){setMessage('请先明确移除所有超出父账号当前权限的旧 grants，再保存权限变更。');return}
    patch.grants=after;
   }
  }
  if(!Object.keys(patch).length){setMessage('没有需要保存的更改');return}
  await api('/auth/accounts/'+chosen,{method:'PATCH',body:JSON.stringify(patch)});
  await load();
  setMessage('账号已更新');
 }
 async function submitCreate(){
  const validation=validateNewAccount({username,name:newName,password});
  setCreateError('');
  setCreateErrors(validation.errors);
  if(validation.firstInvalidField){
   const inputs={username:usernameInput,name:nameInput,password:passwordInput};
   inputs[validation.firstInvalidField].current?.focus();
   return;
  }
  await act(async()=>{
   let created:Account;
   try{created=await api('/auth/accounts',{method:'POST',body:JSON.stringify({parent_id:parent,username,name:newName,password,inherit_all:newAll,grants:newAll?[]:newScopes})})}
   catch(error){const formatted=formatCreateAccountError(error);setCreateError(formatted);setMessage(formatted);return}
   setPassword('');setUsername('');setNewName('');
   await load(created.id);
   setMessage('子账号已创建，可使用统一登录入口');
  });
 }
 return <section className="account-manager"><h2>账号与 API key</h2><p>子账号的有效权限随父账号变化。API key 只显示一次，撤销后立即停止授权。</p><p role="status">{message}</p>
 {treeState==='loading'&&<p role="status">正在检查账号树状态…</p>}
 {treeState==='unregistered'&&<section className="account-setup" aria-labelledby="account-setup-title"><h3 id="account-setup-title">首次启用账号树</h3><p>当前登录身份尚未登记到受管账号树。初始化会将当前身份登记为根账号，并沿用已验证的现有权限；不会自动启用账号树或增加权限。</p>{me?.kind==='human'&&me?.scopes?.includes('desktop:admin')?<button disabled={busy} onClick={()=>act(async()=>{await api('/auth/accounts/bootstrap',{method:'POST',body:JSON.stringify({name:'管理员'})});await load();setMessage('账号树已启用')})}>初始化我的账号树</button>:<p>请联系具有桌面管理员权限的人工用户完成初始化。此页面不会自动执行此操作。</p>}</section>}
 {nodes.length>0&&<div className="account-columns"><aside className="account-tree"><h3>我的账号树</h3>
  <label className="account-tree-filter">筛选账号<input type="search" placeholder="名称或登录名" value={treeFilter} onChange={event=>setTreeFilter(event.target.value)}/></label>
  <button type="button" disabled={!treeFilter} onClick={()=>setTreeFilter('')}>清除筛选</button>
  <p className="muted" role="status">{visibleNodes.length} / {nodes.length} 个账号</p>
  {!visibleNodes.length&&<p role="status">没有匹配的账号</p>}
  <div className="account-tree-list">{visibleNodes.map(n=><button key={n.id} className={n.id===chosen?'active':''} style={{paddingLeft:12+depth(n)*16}} onClick={()=>setChosen(n.id)}>{n.name}<small>{n.username} · {n.enabled?'启用':'停用'}</small></button>)}</div>
 </aside>
 <div className="account-detail">{selected&&<><h3>{selected.name}</h3><p className="muted">{selected.id}</p><label>显示名称<input value={name} onChange={e=>setName(e.target.value)}/></label>
 {canPolicy&&<><label><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>启用账号</label>{selected.parent&&<label><input type="checkbox" checked={inherit} onChange={e=>setInherit(e.target.checked)}/>继承父账号全部权限</label>}{!inherit&&<>{isOwnRoot&&rootCandidates.length>0&&<p>以下权限已由当前登录角色授权，可由根账号显式加入；不会自动启用。</p>}{!isOwnRoot&&rootCandidates.length>0&&<p className="account-locked-hint">灰色的权限由登录角色持有，但根账号尚未采纳，暂时无法授予；请先用根账号登录，在根账号的权限列表勾选采纳后再回来授予。</p>}<Scopes available={editableScopes} locked={isOwnRoot?[]:rootCandidates} selected={grants} setSelected={setGrants}/></>}</>}
 {canManage&&<button disabled={busy} onClick={()=>act(saveAccount)}>保存账号设置</button>}
 {canPolicy&&unavailableGrants.length>0&&<div className="account-stale-grants" aria-label="不可授予的旧权限"><strong>已不可授予的旧权限</strong><p>这些权限仍在账号原始 grants 中，但已超出父账号当前权限，因此目前不生效。保存名称或启停状态不会改动它们；可在此显式移除。</p>{unavailableGrants.map(scope=><button key={scope} disabled={busy} onClick={()=>setGrants(current=>current.filter(value=>value!==scope))}>移除 {labels[scope]||scope}</button>)}</div>}
 <details><summary>当前有效权限</summary><ul>{selected.effective_scopes.map(s=><li key={s}>{labels[s]||s}</li>)}</ul></details>
 {me?.kind==='human'&&(chosen===me.subject||canManage)&&<details><summary>重置密码</summary><p>会撤销该账号现有登录和 API key。新密码至少 12 个字符。</p><input type="password" autoComplete="new-password" aria-label="新密码" value={reset} onChange={e=>setReset(e.target.value)}/><button disabled={busy||reset.length<12} onClick={()=>act(async()=>{await api('/auth/accounts/'+chosen+'/password',{method:'POST',body:JSON.stringify({password:reset})});setReset('');setMessage(chosen===me.subject?'密码已重置，请重新登录':'密码已重置，登录和 API key 已撤销');if(chosen!==me.subject)await load()})}>重置并撤销凭据</button></details>}
 <h3>API key</h3><div className="key-list">{keys.map(key=><div key={key.id}><span>{key.name} · {key.revoked?'已撤销':key.scopes.map((s:string)=>labels[s]||s).join('、')}<small>创建于 {new Date(key.created*1000).toLocaleString()} · <span title={key.id}>ID …{key.id.slice(-8)}</span></small></span>{!key.revoked&&<button disabled={busy} onClick={()=>act(async()=>{await api('/auth/api-keys/'+key.id,{method:'DELETE'});setKeys(await api('/auth/api-keys?owner='+chosen));setMessage('API key 已撤销')})}>撤销</button>}</div>)}</div>
 <label>新 key 名称<input value={keyName} onChange={e=>setKeyName(e.target.value)}/></label><Scopes available={selected.effective_scopes.filter(s=>me?.scopes?.includes(s))} selected={keyScopes} setSelected={setKeyScopes}/><button disabled={busy||!keyName} onClick={()=>act(async()=>{const key=await api('/auth/api-keys',{method:'POST',body:JSON.stringify({owner:chosen,name:keyName,scopes:keyScopes})});setSecret(key.token);setKeyName('');setKeys(await api('/auth/api-keys?owner='+chosen));setMessage('请保存新 key；关闭后无法再次查看')})}>建立 API key</button>
 {secret&&<div className="one-time-key"><input readOnly aria-label="新 API key（仅显示一次）" value={secret}/><button onClick={()=>act(async()=>{await navigator.clipboard.writeText(secret);setMessage('已复制到剪贴板')})}>复制</button><button onClick={()=>setSecret('')}>已保存，关闭</button></div>}</>}
 {canManage&&<details><summary>建立子账号</summary>
  <label>父账号<select value={parent} onChange={e=>{setParent(e.target.value);setNewScopes(['desktop:read'].filter(s=>nodes.find(n=>n.id===e.target.value)?.effective_scopes.includes(s)))}}>{nodes.map(n=><option key={n.id} value={n.id}>{n.name}</option>)}</select></label>
  <label>登录名<input ref={usernameInput} value={username} onChange={e=>setUsername(e.target.value)} autoComplete="off" placeholder="字母、数字、点、横线，下划线" aria-invalid={!!createErrors.username} aria-describedby={'create-username-hint'+(createErrors.username?' create-username-error':'')}/><small className="muted field-hint" id="create-username-hint">{USERNAME_HINT}</small></label>
  {createErrors.username&&<p className="field-error" id="create-username-error" role="alert" style={{color:'var(--danger)'}}>{createErrors.username}</p>}
  <label>显示名称<input ref={nameInput} value={newName} onChange={e=>setNewName(e.target.value)} aria-invalid={!!createErrors.name} aria-describedby={createErrors.name?'create-name-error':undefined}/></label>
  {createErrors.name&&<p className="field-error" id="create-name-error" role="alert" style={{color:'var(--danger)'}}>{createErrors.name}</p>}
  <label>初始密码<input ref={passwordInput} type="password" autoComplete="new-password" value={password} onChange={e=>setPassword(e.target.value)} aria-invalid={!!createErrors.password} aria-describedby={'create-password-hint'+(createErrors.password?' create-password-error':'')}/><small className="muted field-hint" id="create-password-hint">{PASSWORD_HINT}</small></label>
  {createErrors.password&&<p className="field-error" id="create-password-error" role="alert" style={{color:'var(--danger)'}}>{createErrors.password}</p>}
  <label><input type="checkbox" checked={newAll} onChange={e=>setNewAll(e.target.checked)}/>继承父账号全部权限</label>
  {!newAll&&<><Scopes available={parentNode?.effective_scopes||[]} locked={rootCandidates} selected={newScopes} setSelected={setNewScopes}/>{rootCandidates.length>0&&<p className="account-locked-hint">灰色的权限由登录角色持有，但根账号尚未采纳，暂时无法授予新子账号；请先用根账号登录，在根账号的权限列表勾选采纳后再创建。</p>}</>}
  {createError&&<p className="account-create-error" role="alert" style={{color:'var(--danger)'}}>{createError}</p>}
  <button disabled={busy} onClick={()=>void submitCreate()}>创建子账号</button>
 </details>}

 <AccountAudit apiPage={apiPage} identityKey={`${authEpoch}:${me?.subject||''}`} accountLabel={auditAccount}/>
 </div></div>}</section>
}
