/** Quoted TSV for spreadsheet paste: delimiters/newlines remain inside a cell. */
function cell(value) {
  const text = value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  return /[\t\r\n"]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function serializeTSV(headers, records) {
  return [headers, ...records].map(record => record.map(cell).join('\t')).join('\r\n');
}
