import {stringifyExactJSON} from './json.js';
import {serializeTSV} from './tsv.js';

/** Protect free provenance text for spreadsheet paste; keep values as exact JSON. */
export function serializeQueryResultsTSV(rows, tableName) {
  return serializeTSV(['表', '路径', 'Row ID', 'Revision', '值'], rows.map(row => [
    row.table_id == null ? null : tableName(row.table_id),
    row.path, row._row_id, row._revision, stringifyExactJSON(row.values),
  ]), {spreadsheetTextColumns: [0, 1, 2]});
}
