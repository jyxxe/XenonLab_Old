// BigInt is internal only. The XeLibrary wire format remains JSON integer tokens.
const INTEGER_PREFIX = '__xel_exact_integer_';
const JSON_TOKEN = /"(?:\\[\s\S]|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g;

export function parseExactJSON(text) {
  // Validate the original grammar first: replacing tokens must never accept invalid JSON.
  const checked = JSON.parse(text);
  const decoded = JSON.stringify(checked);
  let prefix = INTEGER_PREFIX;
  while (text.includes(prefix) || decoded.includes(prefix)) prefix += '_';
  const marked = text.replace(JSON_TOKEN, token => {
    if (token.startsWith('"')) return token;
    const number = Number(token);
    if (/^-?\d+$/.test(token) && !Number.isSafeInteger(number)) return JSON.stringify(prefix + token);
    if (!Number.isFinite(number)) throw new SyntaxError('JSON 数值超出有限浮点数范围');
    return token;
  });
  return JSON.parse(marked, (_key, value) => typeof value === 'string' && value.startsWith(prefix) ? BigInt(value.slice(prefix.length)) : value);
}

export function stringifyExactJSON(value, space) {
  let prefix = INTEGER_PREFIX;
  for (;;) {
    let collision = false;
    const text = JSON.stringify(value, (key, item) => {
      if (key.includes(prefix) || (typeof item === 'string' && item.includes(prefix))) collision = true;
      return typeof item === 'bigint' ? prefix + item.toString() : item;
    }, space);
    if (collision) { prefix += '_'; continue; }
    if (text === undefined) return text;
    return text.replace(new RegExp(`"${prefix}(-?\\d+)"`, 'g'), (_match, integer) => integer);
  }
}

// Other applications keep their existing JSON representation.
export class LibraryJSONError extends SyntaxError {
  constructor() { super('知识库数据包含无法精确解析的 JSON。'); }
}

export function parseAPIJSON(path, text) {
  if (!/^\/(tables|query|search|theorems)(?:\/|\?|$)/.test(path)) return JSON.parse(text);
  try { return parseExactJSON(text); } catch { throw new LibraryJSONError(); }
}

export function int64Value(value, name = 'int64') {
  let integer;
  if (typeof value === 'bigint') integer = value;
  else if (typeof value === 'number' && Number.isSafeInteger(value)) integer = BigInt(value);
  else if (typeof value === 'string' && /^[+-]?\d+$/.test(value.trim())) integer = BigInt(value.trim());
  else throw new Error(`字段 ${name} 需要精确的十进制整数；不能使用小数、指数或已经失去精度的数值。`);
  if (integer < -(1n << 63n) || integer > (1n << 63n) - 1n) throw new Error(`字段 ${name} 超出 int64 范围（-9223372036854775808 至 9223372036854775807）。`);
  const number = Number(integer);
  return Number.isSafeInteger(number) ? number : integer;
}

export function normalizeRowValues(columns, values) {
  const normalized = {...values};
  for (const column of columns) {
    if (column.type !== 'int64' || !Object.hasOwn(values, column.name)) continue;
    const value = values[column.name];
    if (value === null || value === '') {
      if (!column.nullable) throw new Error(`字段 ${column.name} 不能为空。`);
      normalized[column.name] = null;
    } else normalized[column.name] = int64Value(value, column.name);
  }
  return normalized;
}

export function normalizeColumnDefaults(columns) {
  return columns.map(column => column.type === 'int64' && column.default_value !== null && column.default_value !== undefined
    ? {...column, default_value: int64Value(column.default_value, column.name)} : column);
}
