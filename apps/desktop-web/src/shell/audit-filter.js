/** Keep local search text identical to the four displayed audit columns. */
export function auditDisplayFields(row) {
  return [
    row.at ? new Date(typeof row.at === 'number' ? row.at * 1000 : row.at).toLocaleString() : '—',
    String(row.event ?? ''),
    String(row.actor ?? ''),
    String(row.resource || row.target || ''),
  ];
}

/** Search only loaded rows, preserving the API's order and record identities. */
export function filterAuditRows(rows, query) {
  const needle = query.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter(row => auditDisplayFields(row).some(value => value.toLowerCase().includes(needle)));
}
