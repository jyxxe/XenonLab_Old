import React from 'react';
import {EmptyState} from '../design-system/primitives';
import {Knowledge} from '../Knowledge';
import {Work} from '../Work';
import {EmbeddingQueue} from '../EmbeddingQueue';
import {System} from './System';
import {Settings} from '../Settings';
import {Embedding} from '../Embedding';
import {Development} from './Development';
import {Issues} from './Issues';
import {isRetiredRegistryAppId} from './window-manager.js';
import {HarnessLaunch} from '../HarnessLaunch';
import type {DesktopApp, RegistryApp} from './types';

const chatOrigin =
  (globalThis as {__XEONOLAB_CHAT_ORIGIN__?: string}).__XEONOLAB_CHAT_ORIGIN__ ||
  (import.meta.env?.VITE_CHAT_ORIGIN as string | undefined) ||
  '';

function ChatUnavailable() {
  return <EmptyState icon="✦" title="AI 对话尚未配置" description="OpenWebUI 未提供可用入口。管理员部署服务后，可在系统设置的应用中登记 chat 或 openwebui。" />;
}

export {forgejoBase} from './forgejo-links.js';

/** Built-in native apps. The backend registry only manages external (iframe) apps. */
export function builtinApps(): DesktopApp[] {
  const apps: DesktopApp[] = [
    {
      id: 'knowledge',
      title: '知识库',
      icon: '▤',
      kind: 'native',
      permissions: ['xelibrary:read'],
      defaultSize: [1180, 780],
      minSize: [820, 520],
      singleton: true,
      dock: true,
      description: 'XeLibrary 数据表、检索与定理区',
      menu: [
        {id: 'search', label: '搜索知识库…', intent: {action: 'search'}},
        {id: 'query', label: 'Query Console…', intent: {action: 'query'}},
        {id: 'theorems', label: '定理区', intent: {action: 'theorems'}},
        {id: 'refresh', label: '刷新知识库', intent: {action: 'refresh'}},
      ],
      component: Knowledge as React.ComponentType<any>,
    },
    {
      id: 'chat',
      title: 'AI 对话',
      icon: '✦',
      kind: chatOrigin ? 'iframe' : 'native',
      component: chatOrigin ? undefined : ChatUnavailable,
      origin: chatOrigin || undefined,
      entry: '/',
      sandbox: {allow_scripts: true, allow_forms: true, allow_downloads: false},
      permissions: [],
      defaultSize: [1080, 780],
      minSize: [720, 520],
      singleton: true,
      dock: true,
      description: 'OpenWebUI 对话工作区',
      menu: [{id: 'reload', label: '重新加载对话', action: 'reload'}, {id: 'external', label: '在浏览器中打开', action: 'external'}],
    },
    {
      id: 'development',
      title: '开发',
      icon: '⌘',
      kind: 'native',
      permissions: [],
      defaultSize: [1020, 700],
      minSize: [720, 480],
      singleton: true,
      dock: true,
      description: '仓库、任务与发布入口',
      menu: [{id: 'refresh', label: '刷新任务', action: 'refresh'}],
      component: Development as React.ComponentType<any>,
    },
    {
      id: 'issues',
      title: '任务',
      icon: '⚑',
      kind: 'native',
      permissions: ['dev:read'],
      defaultSize: [1080, 720],
      minSize: [720, 480],
      singleton: true,
      dock: true,
      description: 'Harness 任务与 Issue 状态',
      menu: [{id: 'refresh', label: '刷新任务', action: 'refresh'}],
      component: Issues as React.ComponentType<any>,
    },
    {
      id: 'embedding-queue',
      title: 'Embedding 队列',
      icon: '≡',
      kind: 'native',
      permissions: ['dev:read'],
      defaultSize: [1020, 700],
      minSize: [720, 480],
      singleton: true,
      dock: true,
      description: 'Embedding 请求实时队列与最近完成记录',
      component: EmbeddingQueue as React.ComponentType<any>,
    },
    {
      id: 'system',
      title: '系统状态',
      icon: '◎',
      kind: 'native',
      permissions: [],
      defaultSize: [1080, 720],
      minSize: [720, 480],
      singleton: true,
      dock: true,
      description: '服务、队列与部署状态',
      component: System as React.ComponentType<any>,
    },
    {
      id: 'embedding',
      title: 'Embedding',
      icon: '⟡',
      kind: 'native',
      permissions: [],
      defaultSize: [980, 760],
      minSize: [620, 520],
      singleton: true,
      dock: true,
      description: '文本编码与本人索引队列',
      component: Embedding as React.ComponentType<any>,
    },
    {
      id: 'settings',
      title: '系统设置',
      icon: '⚙',
      kind: 'native',
      permissions: [],
      defaultSize: [920, 700],
      minSize: [680, 480],
      singleton: true,
      dock: true,
      description: '外观、桌面、应用与账号',
      component: Settings as React.ComponentType<any>,
    },
  ];
  // Maintenance shortcut for the DeepSeek Harness Web UI. The entry is offered
  // from every desktop origin; the token-bearing redirect and the Harness UI
  // itself stay reachable only from the lab intranet, so a public-origin visitor
  // can complete the shortcut only when the browser is on the same intranet.
  apps.push({
    id: 'harness',
    title: 'DeepSeek Harness',
    icon: '⌘',
    kind: 'native',
    permissions: [],
    defaultSize: [760, 520],
    minSize: [520, 380],
    singleton: true,
    dock: false,
    description: '内网可用：打开 DeepSeek Harness 维护终端',
    menu: [{id: 'open', label: '打开 DeepSeek Harness', action: 'external'}],
    component: HarnessLaunch as React.ComponentType<any>,
  });
  return apps;
}

export function toDesktopApp(entry: RegistryApp): DesktopApp {
  return {
    id: entry.id,
    title: entry.title,
    icon: entry.icon || '◇',
    kind: 'iframe',
    origin: entry.origin,
    entry: entry.entry || '/',
    sandbox: entry.sandbox,
    permissions: entry.permissions || [],
    defaultSize: entry.default_size || [1000, 700],
    minSize: [480, 360],
    singleton: entry.singleton ?? true,
    dock: false,
    description: entry.description || entry.origin,
    plugin_id: entry.plugin_id,
  };
}

export function mergeApps(registry: RegistryApp[], scopes: string[]): DesktopApp[] {
  const configuredChat = registry.find(entry => ['chat', 'openwebui'].includes(entry.id) && (entry.permissions || []).every(scope => scopes.includes(scope)));
  const builtins = builtinApps().map(app => app.id === 'chat' && configuredChat ? {...toDesktopApp(configuredChat), id: 'chat', title: app.title, dock: true, menu: app.menu} : app).filter(app => app.permissions.every(scope => scopes.includes(scope)));
  const builtinIds = new Set(builtins.map(app => app.id));
  const external = registry
    .filter(entry => !isRetiredRegistryAppId(entry.id) && !builtinIds.has(entry.id) && entry !== configuredChat)
    .map(toDesktopApp)
    .filter(app => app.permissions.every(scope => scopes.includes(scope)));
  return [...builtins, ...external];
}

export function sandboxValue(app: DesktopApp): string {
  const sandbox = app.sandbox ?? {allow_scripts: true, allow_forms: true, allow_downloads: false};
  return [
    'allow-same-origin',
    ...(sandbox.allow_scripts === false ? [] : ['allow-scripts']),
    ...(sandbox.allow_forms === false ? [] : ['allow-forms']),
    ...(sandbox.allow_downloads ? ['allow-downloads'] : []),
  ].join(' ');
}

export function appEntryUrl(app: DesktopApp): string {
  return new URL(app.entry || '/', app.origin).href;
}
