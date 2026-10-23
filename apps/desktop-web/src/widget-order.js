/** Move one enabled widget without mutating the saved preference array. */
export function moveWidget(widgets, id, direction) {
  const index = widgets.indexOf(id), target = index + direction;
  if (![-1, 1].includes(direction) || index < 0 || target < 0 || target >= widgets.length) return widgets;
  const next = [...widgets];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}
