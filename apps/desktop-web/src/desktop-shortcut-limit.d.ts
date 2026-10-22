export const SHORTCUT_LIMIT: number;
export function toggleShortcutApp(selected: unknown, id: string, limit?: number): {selected: string[]; blocked: boolean};
export function shortcutLimitHint(selected: unknown, limit?: number): string;
