/** Apply edits only to principals loaded from the authenticated account tree. */
export function mergeManagedGrants(existing, draft, accounts) {
  const result = { ...(existing || {}) };
  const managed = new Set([...(accounts || []).map(account => account.id), '*']);
  for (const subject of managed) {
    const actions = Array.isArray(draft?.[subject]) ? [...new Set(draft[subject])] : [];
    if (actions.length) result[subject] = actions;
    else delete result[subject];
  }
  return result;
}

export function unknownSubjectsForTree(grants, accounts, treeReady) {
  if (!treeReady) return [];
  const managed = new Set((accounts || []).map(account => account.id));
  return Object.keys(grants || {}).filter(subject => subject !== '*' && !managed.has(subject));
}
