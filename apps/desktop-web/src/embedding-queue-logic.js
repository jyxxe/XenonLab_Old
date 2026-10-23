/**
 * Pure display logic for the Embedding queue app (issue #74).
 * Plain JS so the Node test runner can import it without a bundler.
 */

export const QUEUE_STATUS_LABELS = {running: '运行中', completed: '已完成', failed: '失败'};

export const QUEUE_STATUS_TONES = {running: 'accent', completed: 'success', failed: 'danger'};

export function queueStatusLabel(status) {
  return QUEUE_STATUS_LABELS[status] || status || '—';
}

export function queueStatusTone(status) {
  return QUEUE_STATUS_TONES[status] || 'neutral';
}

export function formatQueueDuration(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function formatQueueTime(seconds) {
  const stamp = typeof seconds === 'number' && Number.isFinite(seconds) ? seconds * 1000 : Date.parse(String(seconds ?? ''));
  if (!Number.isFinite(stamp)) return '—';
  return new Date(stamp).toLocaleString('zh-CN', {hour12: false});
}

export function appendUnique(existing, incoming) {
  const seen = new Set(existing.map(row => row.id));
  return [...existing, ...incoming.filter(row => !seen.has(row.id))];
}

export function summarizeQueue(counts) {
  const safe = counts && typeof counts === 'object' ? counts : {};
  const read = key => (Number.isFinite(safe[key]) ? safe[key] : 0);
  return {running: read('running'), completed: read('completed'), failed: read('failed')};
}
