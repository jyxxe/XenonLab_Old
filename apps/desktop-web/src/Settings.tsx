import React, {useState} from 'react';
import {Accounts} from './Accounts';
import {Permissions} from './Permissions';
import {AppRegistry} from './AppRegistry';
import {DesktopPreferences} from './DesktopPreferences';
import {ACLManager} from './ACLManager';
import type {UserPreferences} from './shell/types';

type API = (path: string, init?: RequestInit) => Promise<any>;

export function Settings({
  api,
  saveLayout,
  preferences,
  applyPreferences,
  refreshApps,
}: {
  api: API;
  saveLayout: () => Promise<void>;
  preferences: UserPreferences;
  applyPreferences: (preferences: Partial<UserPreferences>) => Promise<void>;
  refreshApps: () => Promise<void>;
}) {
  const [tab, setTab] = useState('appearance');
  const tabs: Array<[string, string]> = [
    ['appearance', '外观与桌面'],
    ['apps', '应用'],
    ['accounts', '账号'],
    ['permissions', '身份与权限'],
    ['acl', '知识库 ACL'],
  ];
  return (
    <div className="settings-panel">
      <nav className="settings-tabs" aria-label="设置分类">
        {tabs.map(([key, title]) => (
          <button key={key} type="button" aria-pressed={tab === key} onClick={() => setTab(key)}>
            {title}
          </button>
        ))}
      </nav>
      <div className="settings-body">
        {tab === 'accounts' ? (
          <Accounts api={api} />
        ) : tab === 'permissions' ? (
          <Permissions api={api} />
        ) : tab === 'acl' ? (
          <ACLManager api={api} />
        ) : tab === 'apps' ? (
          <AppRegistry api={api} changed={refreshApps} />
        ) : (
          <DesktopPreferences api={api} preferences={preferences} apply={applyPreferences} saveLayout={saveLayout} />
        )}
      </div>
    </div>
  );
}
