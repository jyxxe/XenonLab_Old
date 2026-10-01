import type React from 'react';
import type {Toast} from '../design-system/primitives';

export type Rect = {x: number; y: number; width: number; height: number};

export type WindowState = Rect & {
  id: string;
  app_id: string;
  title: string;
  minimized: boolean;
  maximized?: boolean;
  restore?: Rect;
};

export type API<T = any> = (path: string, init?: RequestInit) => Promise<T>;
export type APIPage<T = any> = (
  path: string,
  init?: RequestInit,
) => Promise<{data: T; meta: {request_id?: string; next_cursor?: string | null; [key: string]: any}}>;
export type APIRaw = (path: string, init?: RequestInit) => Promise<Response>;

export type Sandbox = {allow_scripts: boolean; allow_forms: boolean; allow_downloads: boolean};

export type DockPreference = 'small' | 'medium' | 'large';

export type UserPreferences = {
  theme: 'system' | 'light' | 'dark';
  accent: 'blue' | 'purple' | 'green' | 'orange';
  wallpaper: 'aurora' | 'ocean' | 'sunset' | 'silver';
  timezone: string;
  widgets: Array<'clock' | 'status'>;
  dock_size: DockPreference;
  dock_autohide: boolean;
  restore_windows: boolean;
  animations: boolean;
  reduced_motion: boolean;
};

export type RegistryApp = {
  id: string;
  title: string;
  origin: string;
  entry: string;
  icon: string;
  default_size: [number, number];
  singleton: boolean;
  permissions?: string[];
  sandbox?: Sandbox;
  revision?: number;
};

export type AppIntent = {id: string; action: 'search' | 'query' | 'theorems' | 'refresh'; query?: string};
export type AppMenuEntry = {id: string; label: string; intent?: Omit<AppIntent, 'id'>; action?: 'refresh' | 'reload' | 'external'};

export type DesktopApp = {
  id: string;
  title: string;
  icon: string;
  kind: 'native' | 'iframe';
  origin?: string;
  entry?: string;
  sandbox?: Sandbox;
  permissions: string[];
  defaultSize: [number, number];
  minSize: [number, number];
  singleton: boolean;
  dock: boolean;
  description?: string;
  menu?: AppMenuEntry[];
  component?: React.ComponentType<AppViewProps>;
};

export type AppViewProps = {
  api: API;
  apiPage: APIPage;
  apiRaw: APIRaw;
  app: DesktopApp;
  apps: DesktopApp[];
  openApp: (id: string, intent?: Omit<AppIntent, 'id'>) => void;
  intent?: AppIntent;
  dockIds: string[];
  togglePin: (id: string) => void;
  movePinned: (id: string, direction: number) => void;
  preferences: UserPreferences;
  applyPreferences: (preferences: Partial<UserPreferences>) => void;
  notify: (toast: Omit<Toast, 'id'>) => void;
  windowId: string;
  saveLayout: () => Promise<void>;
  refreshApps: () => Promise<void>;
};

export type SessionPhase = 'restoring' | 'signed-out' | 'retrying' | 'reauthorize' | 'connected';
