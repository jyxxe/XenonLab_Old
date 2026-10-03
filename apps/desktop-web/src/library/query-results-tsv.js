import {serializeTSV} from './tsv.js';

/** Copy only the returned page, preserving cross-table provenance and JSON. */
export function serializeQueryResultsTSV(rows, tableName) {
  return serializeTSV(['表', '路径', 'Row ID', 'Revision', '值'], rows.map(row => [
    row.table_id == null ? null : tableName(row.table_id),
    row.path, row._row_id, row._revision, JSON.stringify(row.values),
  ]));
}
