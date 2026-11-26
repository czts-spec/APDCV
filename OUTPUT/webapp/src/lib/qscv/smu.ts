/**
 * Модель SMU Keysight B2910BL: измерительные диапазоны тока, связь
 * aperture ↔ NPLC ↔ speedMode (User's Guide), триггерная модель
 * (Programming Guide, Fig. 1-2) и шумовая модель для симуляции.
 */

import type { MeasurementConfig } from "./types";

/** Измерительные диапазоны тока B2900B/BL (А). */
export const CURRENT_RANGES = [
  10e-9, 100e-9, 1e-6, 10e-6, 100e-6, 1e-3, 10e-3, 100e-3,
];

/**
 * Длительность апертуры (с) из паспорта программы (§5).
 * speedMode: short = 0.01 PLC, medium = 0.1 PLC, normal = 1 PLC, long = 10 PLC,
 * auto = 1 PLC (для диапазонов 10/100 нА AUTO даёт 1 PLC),
 * manual: apertureMode 'nplc' → nplc/lineFreq, 'seconds' → aperture_s.
 */
export function apertureFromConfig(cfg: MeasurementConfig): number {
  switch (cfg.speedMode) {
    case "short":
      return 0.01 / cfg.lineFreq_Hz;
    case "medium":
      return 0.1 / cfg.lineFreq_Hz;
    case "normal":
    case "auto":
      return 1 / cfg.lineFreq_Hz;
    case "long":
      return 10 / cfg.lineFreq_Hz;
    case "manual":
      if (cfg.apertureMode === "nplc") {
        if (!(cfg.lineFreq_Hz > 0)) {
          throw new Error("Некорректная частота сети (lineFreq_Hz должна быть > 0)");
        }
        return cfg.nplc / cfg.lineFreq_Hz;
      }
      return cfg.aperture_s;
    default:
      throw new Error(`Неизвестный speedMode: ${String(cfg.speedMode)}`);
  }
}

/**
 * Границы окон [a_j, b_j] от триггера перехода (методика §5, §6).
 *
 * Протокол B (aperture_charge), j = 1..trigCount:
 *   a_j = (j−1)·trigInterval + acqDelay; b_j = a_j + aperture.
 *   Требование aperture ≤ trigInterval — иначе окна перекрываются.
 *
 * Протокол A (kuhn_staircase): одна апертура a_1 = acqDelay.
 */
export function apertureTimes(cfg: MeasurementConfig): { a: number[]; b: number[] } {
  const aperture = apertureFromConfig(cfg);
  if (!(aperture > 0) || !isFinite(aperture)) {
    throw new Error(`Некорректная длительность апертуры: ${aperture} с`);
  }
  if (cfg.protocol === "kuhn_staircase") {
    return { a: [cfg.acqDelay_s], b: [cfg.acqDelay_s + aperture] };
  }
  if (cfg.trigCount < 1) {
    throw new Error("trigCount должен быть ≥ 1");
  }
  if (aperture > cfg.trigInterval_s) {
    throw new Error(
      `Длительность апертуры (${aperture.toFixed(6)} с) превышает период триггера ` +
        `(${cfg.trigInterval_s.toFixed(6)} с): уменьшите апертуру или увеличьте интервал`,
    );
  }
  const a: number[] = [];
  const b: number[] = [];
  for (let j = 1; j <= cfg.trigCount; j++) {
    const aj = (j - 1) * cfg.trigInterval_s + cfg.acqDelay_s;
    a.push(aj);
    b.push(aj + aperture);
  }
  return { a, b };
}

/**
 * Выбор диапазона измерения тока по |I|.
 * fixed: диапазон iRange_A; перегрузка — |iAbs| > 1.05·range.
 * auto: наименьший диапазон ≥ |iAbs|/1.05.
 */
export function pickRange(
  iAbs: number,
  cfg: MeasurementConfig,
): { range: number; overload: boolean } {
  if (cfg.iRangeMode === "fixed") {
    return { range: cfg.iRange_A, overload: iAbs > 1.05 * cfg.iRange_A };
  }
  let range = CURRENT_RANGES[CURRENT_RANGES.length - 1];
  for (const r of CURRENT_RANGES) {
    if (r >= iAbs / 1.05) {
      range = r;
      break;
    }
  }
  return { range, overload: iAbs > 1.05 * range };
}

/** Детерминированный PRNG mulberry32 (один и тот же seed → одна и та же последовательность). */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Нормальное распределение N(0,1), метод Бокса–Мюллера. */
export function gauss(rng: () => number): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = rng();
  while (v === 0) v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

/**
 * Шумовая модель SMU (симуляция): uI(i) = uIOffset_A + uIGainRel·|i|;
 * offset — систематическое смещение нуля прибора.
 */
export function smuNoiseConfig(cfg: MeasurementConfig): {
  uI: (iAbs: number) => number;
  offset: number;
} {
  return {
    uI: (iAbs: number) => cfg.uIOffset_A + cfg.uIGainRel * Math.abs(iAbs),
    offset: cfg.iOffset_A,
  };
}
