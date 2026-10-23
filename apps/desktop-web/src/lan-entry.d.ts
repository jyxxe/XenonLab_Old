export const DEFAULT_LAN_ORIGIN: string;
export function lanEntryOrigin(configured?: unknown): string;
export function lanEntryState(currentOrigin?: unknown, configured?: unknown): {origin: string; current: boolean; hint: string};
export function copyLanEntry(origin: string, clipboard?: {writeText?: (value: string) => Promise<void>}): Promise<boolean>;
