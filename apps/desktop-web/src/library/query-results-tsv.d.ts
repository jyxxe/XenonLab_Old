type QueryResult = {
  table_id?: string | null;
  path?: string | null;
  _row_id?: string | null;
  _revision?: number | null;
  values?: unknown;
};
export function serializeQueryResultsTSV(rows: QueryResult[], tableName: (tableId: string) => string): string;
