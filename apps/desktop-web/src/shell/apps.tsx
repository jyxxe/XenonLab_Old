import React from 'react';
import {EmptyState} from '../design-system/primitives';
import {Knowledge} from '../Knowledge';
import {Work} from '../Work';
import {Dashboard} from '../Dashboard';
import {Settings} from '../Settings';
import {Development} from './Development';
import {Issues} from './Issues';
import type {DesktopApp, RegistryApp} from './types';

const chatOrigin =
  (globalThis as {__XEONOLAB_CHAT_ORIGIN__?: string}).__XEONOLAB_CHAT_ORIGIN__ ||
  (import.meta.env?.VITE_CHAT_ORIGIN as string | undefined) ||
  '';

function ChatUnavailable() {
  return <EmptyState icon="✦" title="AI 对话尚未配置" description="OpenWebUI 未提供可用入口。管理员部署服务后，可在系统设置的应用中登记 chat 或 openwebui。" />;
}

export function forgejoBase(): string {
  return location.hostname === 'xenon.jyxxe.com' ? 'https://xenon.jyxxe.com/dev' : 'https://192.168.0.101:9445';
}

/** Built-in native apps. The backend registry only manages external (iframe) apps. */
export function builtinApps(): DesktopApp[] {
  return [
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
      component: Issues as React.ComponentType<any>,
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
      component: Dashboard as React.ComponentType<any>,
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
    description: entry.origin,
  };
}

export function mergeApps(registry: RegistryApp[], scopes: string[]): DesktopApp[] {
  const configuredChat = registry.find(entry => ['chat', 'openwebui'].includes(entry.id) && (entry.permissions || []).every(scope => scopes.includes(scope)));
  const builtins = builtinApps().map(app => app.id === 'chat' && configuredChat ? {...toDesktopApp(configuredChat), id: 'chat', title: app.title, dock: true} : app).filter(app => app.permissions.every(scope => scopes.includes(scope)));
  const builtinIds = new Set(builtins.map(app => app.id));
  const external = registry
    .filter(entry => !builtinIds.has(entry.id) && entry !== configuredChat)
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
