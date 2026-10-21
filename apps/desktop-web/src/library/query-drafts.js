export const QUERY_EXPRESSION_LIMIT = 8192;
export const QUERY_RECENT_LIMIT = 20;
const VERSION = 1;

export function queryDraftsStorageKey(subject) {
  if (typeof subject !== 'string' || !subject.trim()) return null;
  return `xeonolab.library.${encodeURIComponent(subject)}.query-drafts.v1`;
}

export function queryExpressionLength(value) {
  return Array.from(String(value)).length;
}

function validExpression(value) {
  return typeof value === 'string' && queryExpressionLength(value) <= QUERY_EXPRESSION_LIMIT;
}

function normalizeRecent(value) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  const normalized = [];
  for (const entry of value) {
    if (!entry || !validExpression(entry.expression) || !entry.expression.trim() || seen.has(entry.expression)) continue;
    seen.add(entry.expression);
    normalized.push({expression: entry.expression, updated_at: typeof entry.updated_at === 'string' ? entry.updated_at : ''});
    if (normalized.length === QUERY_RECENT_LIMIT) break;
  }
  return normalized;
}

export function readQueryDrafts(storage, subject) {
  const key = queryDraftsStorageKey(subject);
  if (!key) return {draft: null, recent: [], error: null, ready: false};
  try {
    const raw = storage.getItem(key);
    if (!raw) return {draft: null, recent: [], error: null, ready: true};
    const value = JSON.parse(raw);
    if (value?.version !== VERSION) return {draft: null, recent: [], error: new Error('Unsupported query draft schema version'), ready: false};
    const draft = validExpression(value.draft?.expression)
      ? {expression: value.draft.expression, updated_at: typeof value.draft.updated_at === 'string' ? value.draft.updated_at : ''}
      : null;
    return {draft, recent: normalizeRecent(value.recent), error: null, ready: true};
  } catch (error) {
    return {draft: null, recent: [], error, ready: false};
  }
}

export function writeQueryDrafts(storage, subject, draft, recent) {
  const key = queryDraftsStorageKey(subject);
  if (!key || !validExpression(draft?.expression)) return {error: new Error('No verified account or invalid expression')};
  try {
    storage.setItem(key, JSON.stringify({
      version: VERSION,
      draft: {expression: draft.expression, updated_at: draft.updated_at},
      recent: normalizeRecent(recent),
    }));
    return {error: null};
  } catch (error) {
    return {error};
  }
}

export function addQueryRecent(recent, expression, updatedAt) {
  if (!validExpression(expression) || !expression.trim()) return normalizeRecent(recent);
  return [{expression, updated_at: updatedAt}, ...normalizeRecent(recent).filter(entry => entry.expression !== expression)].slice(0, QUERY_RECENT_LIMIT);
}
