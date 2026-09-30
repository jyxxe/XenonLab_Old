/**
 * Pure keyboard-navigation helpers for Menu / ContextMenu / CommandPalette.
 * Kept as plain JS so the Node test runner can import it directly.
 */

/** Index of the next (direction = 1) or previous (direction = -1) enabled item. */
export function nextEnabledIndex(items, current, direction = 1) {
  const count = items.length;
  if (count === 0) return -1;
  let index = current;
  for (let step = 0; step < count; step += 1) {
    index = (index + direction + count) % count;
    if (!items[index]?.disabled && !items[index]?.separator) return index;
  }
  return -1;
}

/** Index of the first enabled item, used when a menu opens. */
export function firstEnabledIndex(items) {
  return nextEnabledIndex(items, -1, 1);
}

/** Index of the last enabled item, used for ArrowUp from a closed position. */
export function lastEnabledIndex(items) {
  return nextEnabledIndex(items, 0, -1);
}

/**
 * Typeahead: find the next item whose label starts with `query` (case-insensitive).
 * Wraps around and prefers items after `current` so repeated same-letter presses cycle.
 */
export function typeaheadIndex(items, current, query) {
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) return -1;
  const count = items.length;
  for (let step = 1; step <= count; step += 1) {
    const index = (current + step) % count;
    const item = items[index];
    if (item?.label && !item.disabled && !item.separator && String(item.label).toLowerCase().startsWith(needle)) {
      return index;
    }
  }
  return -1;
}
