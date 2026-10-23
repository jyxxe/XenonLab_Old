import React, {useEffect, useId, useState} from 'react';
import {Badge, Button, EmptyState} from './design-system/primitives';
import {agentPrompts, networkLabel, type PromptNetwork} from './agent-prompts';

type AdminPrompt = {id: string; title: string; file: string; body: string};
type AdminState = 'loading' | 'ready' | 'forbidden' | 'error';

/** Admin-only, credential-bearing prompts loaded from a protected runtime directory. */
function AdminPrompts({apiPage}: {apiPage: (path: string, init?: RequestInit) => Promise<{data: any; meta: Record<string, any>}>}) {
  const [prompts, setPrompts] = useState<AdminPrompt[]>([]);
  const [state, setState] = useState<AdminState>('loading');
  const [selectedId, setSelectedId] = useState('');
  const [status, setStatus] = useState('');
  useEffect(() => {
    let active = true;
    setState('loading');
    apiPage('/agent-prompts').then(page => {
      if (!active) return;
      const rows = Array.isArray(page?.data) ? page.data.filter((row: any) => row && typeof row.body === 'string') : [];
      setPrompts(rows);
      setSelectedId(rows[0]?.id ?? '');
      setState('ready');
    }).catch((error: any) => {
      if (!active) return;
      setState(error?.status === 403 || error?.status === 401 ? 'forbidden' : 'error');
    });
    return () => { active = false; };
  }, [apiPage]);
  const selected = prompts.find(prompt => prompt.id === selectedId) ?? prompts[0];
  const copy = async () => {
    try {
      if (!selected || !navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(selected.body);
      setStatus(`已复制《${selected.title}》全文。`);
    } catch {
      setStatus('复制失败：浏览器未提供剪贴板权限，请手动选择预览文本。');
    }
  };
  if (state === 'loading') return <p className="muted" role="status">正在读取管理员提示词…</p>;
  if (state === 'forbidden') return <p className="muted">管理员凭据版提示词仅对具有 desktop:admin 权限的账号显示。</p>;
  if (state === 'error') return <p className="muted">管理员提示词读取失败；公开提示词不受影响。</p>;
  if (!prompts.length) return <p className="muted">服务端尚未放置管理员凭据版提示词（运维文件，不入库）。</p>;
  return <div className="system-prompts-admin" data-testid="admin-prompts">
    <p className="system-prompts-note" role="note">⚠ 以下提示词含真实凭据，仅管理员可见；不得提交到 Git、Issue、PR、日志或聊天输出，也不得转给未授权 Agent。</p>
    <p className="muted">管理员凭据版（{prompts.length}）</p>
    <ul className="system-prompts-admin-list">
      {prompts.map(prompt => <li key={prompt.id}><button type="button" className="system-prompt-item" aria-current={selected.id === prompt.id ? 'true' : undefined} onClick={() => { setSelectedId(prompt.id); setStatus(''); }}>{prompt.title}</button></li>)}
    </ul>
    <div className="system-prompts-actions"><Button onClick={() => { void copy(); }}>复制全文</Button></div>
    <pre className="system-prompt-body" data-testid="admin-prompt-preview">{selected.body}</pre>
    <p className="muted system-prompts-status" role="status" aria-live="polite">{status}</p>
  </div>;
}

/** Agent prompt catalogue: bundled public prompts plus the admin-only runtime section. */
export function AgentPrompts({apiPage}: {apiPage: (path: string, init?: RequestInit) => Promise<{data: any; meta: Record<string, any>}>}) {
  return <div className="system-prompts">
    <PublicPrompts/>
    <AdminPrompts apiPage={apiPage}/>
  </div>;
}

function promptNetworkTone(network: PromptNetwork): 'success' | 'warning' {
  return network === 'lan' ? 'success' : 'warning';
}

/**
 * Read-only prompt catalogue. The bodies are bundled at build time, so the panel
 * never fetches a static docs route and behaves the same on LAN and public origins.
 * Selecting an entry only changes local state; copy/download stay local as well.
 */
function PublicPrompts() {
  const [selectedId, setSelectedId] = useState(() => agentPrompts[0]?.id ?? '');
  const [status, setStatus] = useState('');
  const listTitleId = useId();
  const selected = agentPrompts.find(prompt => prompt.id === selectedId) ?? agentPrompts[0];

  useEffect(() => { setStatus(''); }, [selectedId]);

  if (!selected) return <EmptyState title="提示词清单为空" description="docs/agent-prompts/manifest.json 没有可展示的提示词。"/>;

  const copy = async () => {
    try {
      if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(selected.body);
      setStatus(`已复制《${selected.title}》全文到剪贴板。`);
    } catch {
      setStatus('复制失败：浏览器未提供剪贴板权限，请选中预览文本手动复制。');
    }
  };

  const download = () => {
    try {
      const blob = new Blob([selected.body], {type: 'text/markdown;charset=utf-8'});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = selected.file;
      link.rel = 'noopener';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setStatus(`已开始下载 ${selected.file}。`);
    } catch {
      setStatus('下载失败：当前浏览器不支持本地文件下载。');
    }
  };

  return <>
    <header className="app-head">
      <div>
        <h2>提示词</h2>
        <p>仓库 docs/agent-prompts 中的 Agent 提示词，随桌面构建打包；内网与公网入口都可读取，不依赖静态文件路由。</p>
      </div>
    </header>
    <p className="system-prompts-note">提示词本身不含任何凭据；凭据只存放在私有 0600 身份文件中，由各 Agent 自行读取。请勿把 token、密码或私钥粘贴到提示词或对话里。</p>
    <div className="system-prompts-layout">
      <section className="system-prompts-list" aria-labelledby={listTitleId}>
        <h3 id={listTitleId}>提示词清单（{agentPrompts.length}）</h3>
        <ul>
          {agentPrompts.map(prompt => <li key={prompt.id}>
            <button type="button" className="system-prompt-item" data-testid="agent-prompt-item" aria-current={selected.id === prompt.id ? 'true' : undefined} onClick={() => setSelectedId(prompt.id)}>
              <span className="system-prompt-title">{prompt.title}</span>
              <span className="system-prompt-meta"><Badge tone={promptNetworkTone(prompt.network)}>{networkLabel(prompt.network)}</Badge><span>{prompt.audience}</span></span>
              <span className="system-prompt-summary">{prompt.summary}</span>
            </button>
          </li>)}
        </ul>
      </section>
      <article className="system-prompts-preview" aria-label={`${selected.title} 预览`}>
        <header className="system-prompts-preview-head">
          <div>
            <h3>{selected.title}</h3>
            <p className="muted">{selected.file} · {selected.audience} · <Badge tone={promptNetworkTone(selected.network)}>{networkLabel(selected.network)}</Badge></p>
          </div>
          <div className="system-prompts-actions">
            <Button data-testid="agent-prompt-copy" onClick={() => { void copy(); }}>复制全文</Button>
            <Button data-testid="agent-prompt-download" onClick={download}>下载 .md</Button>
          </div>
        </header>
        <pre className="system-prompt-body" data-testid="agent-prompt-preview" tabIndex={0} aria-label={`${selected.title} Markdown 正文`}>{selected.body}</pre>
        <p className="muted system-prompts-status" role="status" aria-live="polite">{status}</p>
      </article>
    </div>
  </>;
}

