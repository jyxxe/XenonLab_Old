import {Icon} from './design-system/Icon';
import {Button} from './design-system/primitives';
import React, {useEffect, useState} from 'react';
import type {PreferenceApplyOptions, UserPreferences} from './shell/types';

type API = (path: string, init?: RequestInit) => Promise<any>;
type Sandbox = {allow_scripts: boolean; allow_forms: boolean; allow_downloads: boolean};
type PluginApp = {
  id: string;
  title: string;
  origin: string;
  entry: string;
  icon: string;
  default_size: [number, number];
  singleton: boolean;
  sandbox: Sandbox;
};
type PluginWidget = {
  id: string;
  title: string;
  origin: string;
  entry: string;
  icon: string;
  height: number;
};
type Plugin = {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  homepage: string;
  icon: string;
  permissions: string[];
  app: PluginApp | null;
  widget: PluginWidget | null;
  revision: number;
  installed: boolean;
  enabled: boolean;
  installed_version: string | null;
  update_available: boolean;
  missing_scopes: string[];
};

const EXAMPLE = `{
  "id": "sample-widget",
  "name": "示例组件",
  "version": "1.0.0",
  "description": "由独立 HTTPS 来源提供的桌面组件。",
  "author": "example",
  "permissions": [],
  "widget": {
    "id": "sample",
    "title": "示例",
    "origin": "https://plugin.example.com",
    "entry": "/widget",
    "icon": "◈",
    "height": 180
  }
}`;

export function Plugins({
  api,
  preferences,
  applyPreferences,
  refreshApps,
}: {
  api: API;
  preferences: UserPreferences;
  applyPreferences: (patch: Partial<UserPreferences>, options?: PreferenceApplyOptions) => Promise<void>;
  refreshApps: () => Promise<void>;
}) {
  const [items, setItems] = useState<Plugin[]>([]);
  const [scopes, setScopes] = useState<string[]>([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState('');
  const [ready, setReady] = useState(false);

  const selectedWidgets = preferences.plugin_widgets || [];

  async function load() {
    const [catalog, me] = await Promise.all([api('/plugins'), api('/users/me')]);
    setItems(Array.isArray(catalog) ? catalog : []);
    setScopes(Array.isArray(me.scopes) ? me.scopes : []);
    setReady(true);
  }

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setMessage('');
    try {
      await action();
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    act(load);
  }, []);

  async function refreshCatalog() {
    await load();
    await refreshApps();
  }

  function forgetWidget(pluginId: string) {
    if (!selectedWidgets.includes(pluginId)) return Promise.resolve();
    return applyPreferences({plugin_widgets: selectedWidgets.filter(id => id !== pluginId)});
  }

  async function install(pluginId: string) {
    await api(`/plugins/${pluginId}/install`, {method: 'POST'});
    await refreshCatalog();
    setMessage('插件已安装到当前账户');
  }

  async function uninstall(pluginId: string) {
    await api(`/plugins/${pluginId}/install`, {method: 'DELETE'});
    await forgetWidget(pluginId);
    await refreshCatalog();
    setMessage('插件已从当前账户卸载');
  }

  async function toggleEnabled(plugin: Plugin) {
    await api(`/plugins/${plugin.id}/install`, {
      method: 'PATCH',
      body: JSON.stringify({enabled: !plugin.enabled}),
    });
    await refreshCatalog();
  }

  async function toggleWidget(plugin: Plugin) {
    const next = selectedWidgets.includes(plugin.id)
      ? selectedWidgets.filter(id => id !== plugin.id)
      : [...selectedWidgets, plugin.id].slice(0, 8);
    await applyPreferences({plugin_widgets: next});
    setMessage(next.includes(plugin.id) ? '桌面组件已显示' : '桌面组件已隐藏');
  }

  async function publish() {
    const body = JSON.parse(draft);
    await api('/plugins', {method: 'POST', body: JSON.stringify(body)});
    setDraft('');
    await refreshCatalog();
    setMessage('插件已发布到云端目录');
  }

  async function remove(plugin: Plugin) {
    await api(`/plugins/${plugin.id}`, {
      method: 'DELETE',
      headers: {'If-Match': `"revision:${plugin.revision}"`},
    });
    await forgetWidget(plugin.id);
    await refreshCatalog();
    setMessage('插件已从目录删除');
  }

  const admin = scopes.includes('desktop:admin');

  return (
    <section className="plugin-panel">
      <header className="plugin-heading">
        <div>
          <h2>插件</h2>
          <p>插件是声明式清单：应用与桌面组件都从独立 HTTPS 来源加载，并在沙箱 iframe 中运行。平台不会在服务端执行插件代码，也不会把登录令牌交给插件。</p>
        </div>
      </header>
      <p role="status" className="plugin-status">{message || (ready ? '' : '正在读取插件目录…')}</p>

      {ready && items.length === 0 ? (
        <p className="muted">目录中还没有插件。拥有桌面管理权限的账号可以在下方发布清单。</p>
      ) : (
        <div className="plugin-grid">
          {items.map(plugin => {
            const widgetSelected = selectedWidgets.includes(plugin.id);
            return (
              <article className="plugin-card" key={plugin.id}>
                <header>
                  <span className="plugin-icon" aria-hidden="true"><Icon value={plugin.icon} size={22} /></span>
                  <div>
                    <h3>{plugin.name} <small>v{plugin.version}</small></h3>
                    <p className="plugin-author">{plugin.author || '未注明作者'}</p>
                  </div>
                </header>
                <p className="plugin-description">{plugin.description || '没有说明。'}</p>
                <div className="plugin-badges">
                  {plugin.app && <span>应用 · {plugin.app.title}</span>}
                  {plugin.widget && <span>桌面组件 · {plugin.widget.title}</span>}
                  {plugin.update_available && <span className="plugin-update">可更新至 v{plugin.version}</span>}
                </div>
                {plugin.permissions.length > 0 && (
                  <p className="plugin-permissions">需要权限：{plugin.permissions.map(scope => <code key={scope}>{scope}</code>)}</p>
                )}
                {plugin.missing_scopes.length > 0 && (
                  <p className="plugin-warning" role="alert">当前账号缺少 {plugin.missing_scopes.join('、')}，无法安装、更新或启用。</p>
                )}
                <div className="plugin-actions">
                  <Button
                    size="sm"
                    variant={plugin.installed ? 'ghost' : 'primary'}
                    disabled={busy || plugin.missing_scopes.length > 0}
                    onClick={() => act(() => install(plugin.id))}
                  >
                    {plugin.installed ? (plugin.update_available ? '更新' : '重新安装') : '安装'}
                  </Button>
                  {plugin.installed && (
                    <>
                      <label className="plugin-check">
                        <input
                          type="checkbox"
                          checked={plugin.enabled}
                          disabled={busy || (!plugin.enabled && plugin.missing_scopes.length > 0)}
                          onChange={() => act(() => toggleEnabled(plugin))}
                        />
                        启用
                      </label>
                      {plugin.widget && (
                        <label className="plugin-check">
                          <input
                            type="checkbox"
                            checked={widgetSelected}
                            disabled={busy || !plugin.enabled}
                            onChange={() => act(() => toggleWidget(plugin))}
                          />
                          显示在桌面
                        </label>
                      )}
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => act(() => uninstall(plugin.id))}>卸载</Button>
                    </>
                  )}
                  {admin && (
                    <Button size="sm" variant="ghost" disabled={busy} onClick={() => act(() => remove(plugin))}>从目录删除</Button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}

      {admin && (
        <details className="plugin-publish">
          <summary>发布插件到目录（桌面管理权限）</summary>
          <p className="muted">清单只会被保存和校验；平台不会下载或执行插件代码。应用与组件来源必须是独立的 HTTPS 来源。</p>
          <textarea
            aria-label="插件清单 JSON"
            value={draft}
            spellCheck={false}
            placeholder={EXAMPLE}
            onChange={event => setDraft(event.target.value)}
          />
          <div className="ds-button-row">
            <Button
              size="sm"
              variant="primary"
              disabled={busy || !draft.trim()}
              onClick={() => act(publish)}
            >发布清单</Button>
            <Button size="sm" variant="ghost" onClick={() => setDraft(EXAMPLE)}>填入示例</Button>
          </div>
        </details>
      )}
    </section>
  );
}
