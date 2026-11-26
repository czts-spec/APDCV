/**
 * Форматирование величин в инженерной нотации (SCI/ENG): 10 pКл, 1.000 нФ, 20.0 мс.
 */

const PREFIXES: [number, string][] = [
  [1e12, "Т"],
  [1e9, "Г"],
  [1e6, "М"],
  [1e3, "к"],
  [1, ""],
  [1e-3, "м"],
  [1e-6, "мк"],
  [1e-9, "н"],
  [1e-12, "п"],
  [1e-15, "ф"],
];

export function formatEng(value: number, unit: string, digits = 3): string {
  if (!isFinite(value)) return "—";
  if (value === 0) return `0 ${unit}`;
  const abs = Math.abs(value);
  for (const [factor, prefix] of PREFIXES) {
    if (abs >= factor) {
      const scaled = value / factor;
      const mag = Math.floor(Math.log10(Math.abs(scaled)));
      const decimals = Math.max(0, digits - 1 - mag);
      return `${scaled.toFixed(decimals)} ${prefix}${unit}`;
    }
  }
  // меньше 1 фемто — экспоненциальная запись
  return `${value.toExponential(2)} ${unit}`;
}

/** Форматирование секунд: 100 мкс, 20.0 мс, 1.000 с */
export function formatSeconds(s: number, digits = 3): string {
  return formatEng(s, "с", digits);
}

/** Форматирование ампер */
export function formatAmps(a: number, digits = 3): string {
  return formatEng(a, "А", digits);
}

/** Форматирование фарад */
export function formatFarads(f: number, digits = 3): string {
  return formatEng(f, "Ф", digits);
}

/** Форматирование кулононов */
export function formatCoulombs(q: number, digits = 3): string {
  return formatEng(q, "Кл", digits);
}

/** Форматирование джоулей */
export function formatJoules(j: number, digits = 3): string {
  return formatEng(j, "Дж", digits);
}

/** Форматирование вольт */
export function formatVolts(v: number, digits = 3): string {
  return formatEng(v, "В", digits);
}

/** Компактное научное представление для таблиц: 1.23e-9 */
export function formatSci(value: number, digits = 3): string {
  if (!isFinite(value)) return "—";
  return value.toExponential(digits - 1);
}
