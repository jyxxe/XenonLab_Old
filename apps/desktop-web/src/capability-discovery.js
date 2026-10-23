// Capabilities decide availability. Existing descriptors keep URLs, sandbox,
// native adapters and persisted desktop ids unchanged.
export function discoverDesktop(capabilities, registry, widgets, scopes) {
  const allowed = (Array.isArray(capabilities) ? capabilities : []).filter(item =>
    item && item.enabled === true && item.runtime === 'ui' &&
    Array.isArray(item.permissions) && item.permissions.every(scope => scopes.includes(scope)));
  const builtinIds = kind => new Set(allowed.filter(item => item.source === 'builtin' && item.kind === kind &&
    typeof item.id === 'string' && item.id.startsWith(`builtin.platform.${kind}.`))
    .map(item => item.id.slice(`builtin.platform.${kind}.`.length)));
  const matches = (entry, kind) => typeof entry.id === 'string' && entry.id.length > 0 && allowed.some(item => item.kind === kind && item.desktop_id === entry.id &&
    (entry.plugin_id ? item.source === 'plugin' && item.plugin_id === entry.plugin_id : item.source === 'registered'));
  return {
    builtinApps: builtinIds('app'),
    builtinWidgets: builtinIds('widget'),
    apps: (Array.isArray(registry) ? registry : []).filter(entry => entry && matches(entry, 'app')),
    widgets: (Array.isArray(widgets) ? widgets : []).filter(entry => entry && matches(entry, 'widget')),
  };
}
