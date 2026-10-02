// Month grid math for the calendar widget, Monday-first. Pure module so the
// unit tests can exercise it without a DOM.

// Returns six weeks (rows) of seven day cells. Cells outside the month carry
// inMonth=false with their overflowing day number.
export function monthMatrix(year, month, weekStart = 1) {
  if (!Number.isInteger(year) || !Number.isInteger(month) || month < 0 || month > 11) {
    throw new RangeError('monthMatrix expects a year and a 0-based month');
  }
  const firstDay = new Date(year, month, 1);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const daysInPreviousMonth = new Date(year, month, 0).getDate();
  const offset = (firstDay.getDay() - weekStart + 7) % 7;

  const weeks = [];
  let cursor = 1 - offset;
  for (let row = 0; row < 6; row += 1) {
    const week = [];
    for (let col = 0; col < 7; col += 1) {
      const day = cursor;
      if (day < 1) {
        week.push({day: daysInPreviousMonth + day, inMonth: false});
      } else if (day > daysInMonth) {
        week.push({day: day - daysInMonth, inMonth: false});
      } else {
        week.push({day, inMonth: true});
      }
      cursor += 1;
    }
    weeks.push(week);
  }
  return weeks;
}

// Resolves the visible day in a target IANA timezone ("system" uses the
// browser zone). Returns {year, month (0-based), day} or null on failure.
export function zonedToday(timezone, now = new Date()) {
  let formatter;
  try {
    formatter = new Intl.DateTimeFormat('en-CA', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      timeZone: timezone && timezone !== 'system' ? timezone : undefined,
    });
  } catch {
    return null;
  }
  const formatted = formatter.format(now);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(formatted);
  if (!match) return null;
  return {year: Number(match[1]), month: Number(match[2]) - 1, day: Number(match[3])};
}
