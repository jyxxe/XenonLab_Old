import type {RegistryApp, PluginWidget} from './shell/types';
export function discoverDesktop(capabilities: unknown, registry: unknown, widgets: unknown, scopes: string[]): {
  builtinApps: Set<string>;
  builtinWidgets: Set<string>;
  apps: RegistryApp[];
  widgets: PluginWidget[];
};
