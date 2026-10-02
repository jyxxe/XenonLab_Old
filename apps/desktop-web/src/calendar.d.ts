export function monthMatrix(year: number, month: number, weekStart?: number): Array<Array<{day: number; inMonth: boolean}>>;
export function zonedToday(timezone: string, now?: Date): {year: number; month: number; day: number} | null;
