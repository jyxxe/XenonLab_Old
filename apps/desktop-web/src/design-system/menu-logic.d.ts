export type MenuLogicItem = {label?: unknown; disabled?: boolean; separator?: boolean};

export function nextEnabledIndex(items: MenuLogicItem[], current: number, direction?: number): number;
export function firstEnabledIndex(items: MenuLogicItem[]): number;
export function lastEnabledIndex(items: MenuLogicItem[]): number;
export function typeaheadIndex(items: MenuLogicItem[], current: number, query: string): number;
