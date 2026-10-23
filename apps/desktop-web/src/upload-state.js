// Library-instance memory only. Files never enter storage or a global cache.
const EMPTY = Object.freeze({resume: null, busy: false, progress: null, message: ''});
export function createUploadStore() {
  const states = new Map(), listeners = new Map();
  let alive = true;
  return {
    get alive() { return alive; },
    get(key) { return states.get(key) || EMPTY; },
    set(key, patch) {
      if (!alive) return;
      const next = {...(states.get(key) || EMPTY), ...patch};
      // Keep only ongoing/recoverable work, never an ever-growing history of visited rows.
      if (!next.resume && !next.busy && !listeners.get(key)?.size) states.delete(key);
      else states.set(key, next);
      for (const notify of listeners.get(key) || []) notify();
    },
    subscribe(key, notify) {
      if (!listeners.has(key)) listeners.set(key, new Set());
      const set = listeners.get(key); set.add(notify);
      return () => { set.delete(notify); if (!set.size) { listeners.delete(key); const state=states.get(key); if (!state?.resume && !state?.busy) states.delete(key); } };
    },
    dispose() { alive = false; states.clear(); listeners.clear(); },
  };
}
