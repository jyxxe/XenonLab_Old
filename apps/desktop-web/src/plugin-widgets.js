/**
 * Select installed plugin widgets in the user's chosen order.
 *
 * Preferences can name a widget whose plugin was uninstalled, disabled, or is no
 * longer available to this account. Rendering only server-provided widgets keeps
 * stale preference ids harmless, and de-duplicating avoids two iframes for the
 * same plugin.
 */
export function selectedPluginWidgets(selected, widgets) {
  if (!Array.isArray(selected) || !Array.isArray(widgets)) return [];
  const available = new Map();
  for (const widget of widgets) {
    if (widget && typeof widget === 'object' && typeof widget.plugin_id === 'string') {
      available.set(widget.plugin_id, widget);
    }
  }
  const result = [];
  const seen = new Set();
  for (const id of selected) {
    if (typeof id !== 'string' || seen.has(id)) continue;
    seen.add(id);
    const widget = available.get(id);
    if (widget) result.push(widget);
  }
  return result;
}
