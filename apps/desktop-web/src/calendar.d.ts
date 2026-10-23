export type CalendarMonth = {year: number; month: number};
export function calendarToday(now: Date, timezone: string): CalendarMonth & {day: number};
export function shiftCalendarMonth(month: CalendarMonth, offset: number): CalendarMonth;
export function calendarMonth(month: CalendarMonth): {weekday: number; days: number; label: string};
