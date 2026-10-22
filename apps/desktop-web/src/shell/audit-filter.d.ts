export type AuditRecord = {at?: number | string; event?: string; actor?: string; resource?: string; target?: string};
export function auditDisplayFields(row: AuditRecord): string[];
export function filterAuditRows<T extends AuditRecord>(rows: T[], query: string): T[];
