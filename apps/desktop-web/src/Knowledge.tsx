import React from 'react';
import type {API, APIPage, APIRaw, AppIntent} from './shell/types';
import {LibraryApp} from './library/LibraryApp';

/**
 * XeLibrary application entry. The layout, navigation, search, query console and
 * inspector all live under `src/library/`; this module keeps the historical
 * `Knowledge` export so the desktop shell and the library-origin page are unchanged.
 */
export function Knowledge({api, apiPage, apiRaw, intent}: {api: API; apiPage: APIPage; apiRaw: APIRaw; intent?: AppIntent}) {
  return <LibraryApp api={api} apiPage={apiPage} apiRaw={apiRaw} intent={intent} />;
}
