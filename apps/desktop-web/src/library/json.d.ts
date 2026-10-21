export function parseExactJSON(text: string): any;
export function stringifyExactJSON(value: any, space?: string | number): string;
export class LibraryJSONError extends SyntaxError { constructor(); }
export function parseAPIJSON(path: string, text: string): any;
export function int64Value(value: unknown, name?: string): number | bigint;
export function normalizeRowValues(columns: Array<{name: string; type: string; nullable: boolean}>, values: Record<string, any>): Record<string, any>;
export function normalizeColumnDefaults<T extends {name: string; type: string; default_value: any}>(columns: T[]): T[];
