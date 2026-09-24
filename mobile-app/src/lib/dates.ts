// Date-only profile values use local noon to avoid UTC conversions shifting the day.
export function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12);
}

export function tomorrow(): Date {
  const result = startOfToday();
  result.setDate(result.getDate() + 1);
  return result;
}

export function todayText(): string {
  return dateText(startOfToday());
}

export function parseDate(value: string): Date {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(year, month - 1, day, 12);
}

export function dateText(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function displayDate(value: string): string {
  const date = parseDate(value);
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

export function initialPickerDate(value: string, minimumDate?: Date, maximumDate?: Date): Date {
  const parsed = value ? parseDate(value) : new Date(NaN);
  let result = Number.isNaN(parsed.getTime()) ? minimumDate ?? maximumDate ?? startOfToday() : parsed;
  if (minimumDate && result < minimumDate) result = minimumDate;
  if (maximumDate && result > maximumDate) result = maximumDate;
  return new Date(result.getTime());
}
