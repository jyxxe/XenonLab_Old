/**
 * Shortcut-app selection rules for Desktop Preferences.
 *
 * The saved list is capped at 12 entries. Before this module the 13th selection
 * silently replaced the oldest entry (`slice(-12)`), so the user lost an app
 * without any feedback (issue #258). Keep the cap, but make the limit visible and
 * refuse the extra selection instead of replacing silently.
 */
export const SHORTCUT_LIMIT = 12;

/** Toggle one app id in the saved shortcut list, reporting whether it was blocked. */
export function toggleShortcutApp(selected, id, limit = SHORTCUT_LIMIT) {
  const list = Array.isArray(selected) ? selected.filter(item => typeof item === 'string' && item) : [];
  if (typeof id !== 'string' || !id) return {selected: list, blocked: false};
  if (list.includes(id)) return {selected: list.filter(item => item !== id), blocked: false};
  if (list.length >= limit) return {selected: list, blocked: true};
  return {selected: [...list, id], blocked: false};
}

/** Visible helper text with the current count and the cap. */
export function shortcutLimitHint(selected, limit = SHORTCUT_LIMIT) {
  const count = Array.isArray(selected) ? selected.length : 0;
  return `已选 ${count}/${limit} 项；最多 ${limit} 项，满额时请先取消一个已选应用。`;
}
