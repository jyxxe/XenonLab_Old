import React from 'react';
import type {API, APIPage, APIRaw, AppIntent} from './shell/types';
import type {DesktopApp} from './shell/types';
import {LibraryApp} from './library/LibraryApp';

/**
 * XeLibrary application entry. The layout, navigation, search, query console and
 * inspector all live under `src/library/`; this module keeps the historical
 * `Knowledge` export so the desktop shell and the library-origin page are unchanged.
 */
export function Knowledge({api, apiPage, apiRaw, intent, apps, openApp, notify, authEpoch = 0}: {
  api: API;
  apiPage: APIPage;
  apiRaw: APIRaw;
  intent?: AppIntent;
  apps: DesktopApp[];
  openApp: (id: string, intent?: Omit<AppIntent, 'id'>) => void;
  notify: (toast: {title: string; tone?: 'info' | 'success' | 'danger'}) => void;
  authEpoch?: number;
}) {
  return (
    <LibraryApp
      api={api}
      apiPage={apiPage}
      apiRaw={apiRaw}
      intent={intent}
      apps={apps}
      openApp={openApp}
      notify={notify}
      authEpoch={authEpoch}
    />
  );
}
