// Baseline 2aabf62 parser, used only to reproduce Issue 159 before the fixed-source phase.
export function parseFieldFilters(text) {
  const filters = {};
  const errors = [];
  for (const line of String(text || '').split(/\n+/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const match = /^([^=\s]+)\s*=\s*(.+)$/.exec(trimmed);
    if (!match) {
      errors.push(`无法解析「${trimmed}」，请使用 字段 = 值`);
      continue;
    }
    filters[match[1]] = match[2];
  }
  return {filters, errors};
}

