/** Quoted TSV: the default retains the existing scalar/JSON byte contract. */
function cell(value, spreadsheetText) {
  let text = value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  if (spreadsheetText && typeof value === 'string') {
    // Quoting separates cells but does not force spreadsheet text semantics.
    // Prefix only free text that could become a formula/control-led cell after
    // whitespace is stripped; retain every original character after the marker.
    const leading = text.match(/^[\s\p{Cc}\p{Cf}]*/u)[0];
    if (/[\p{Cc}\p{Cf}]/u.test(leading) || /^[=+\-@]/.test(text.slice(leading.length))) text = "'" + text;
  }
  return /[\t\r\n"]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function serializeTSV(headers, records, {spreadsheetTextColumns = []} = {}) {
  const protectedColumns = new Set(spreadsheetTextColumns);
  return [headers, ...records].map(record => record.map((value, index) => cell(value, protectedColumns.has(index))).join('\t')).join('\r\n');
}
