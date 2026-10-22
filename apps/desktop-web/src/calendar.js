// Calendar arithmetic uses UTC only to describe a civil month. The configured
// timezone is used solely to derive today's civil date from the current instant.
export function calendarToday(now, timezone) {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone === 'system' ? undefined : timezone,
      year: 'numeric', month: 'numeric', day: 'numeric',
    }).formatToParts(now);
    const value = type => Number(parts.find(part => part.type === type)?.value);
    return {year: value('year'), month: value('month'), day: value('day')};
  } catch {
    return {year: now.getFullYear(), month: now.getMonth() + 1, day: now.getDate()};
  }
}

export function shiftCalendarMonth({year, month}, offset) {
  const total = year * 12 + month - 1 + offset;
  const nextYear = Math.floor(total / 12);
  return {year: nextYear, month: total - nextYear * 12 + 1};
}

export function calendarMonth({year, month}) {
  // setUTCFullYear avoids Date.UTC's special interpretation of years 0–99.
  const first = new Date(0);
  first.setUTCFullYear(year, month - 1, 1);
  const last = new Date(0);
  last.setUTCFullYear(year, month, 0);
  return {weekday: first.getUTCDay(), days: last.getUTCDate(), label: `${year}年${month}月`};
}
