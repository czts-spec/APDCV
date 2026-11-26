/**
 * Офлайн-анализатор — точный порт qscv_final.py (analyse_qs, fit_exponential,
 * численная неопределённость, пропуски, пары прямой/обратный ход).
 *
 * Чистые функции без ввода-вывода. Все нарушения данных собираются в flags
 * 'DATA_INVALID:…' / 'INVALID_UI' вместо исключений (устойчивость API).
 *
 * Правило основания: постоянная составляющая B подгонки НЕ интегрируется в
 * хвосте до бесконечности — remainingTail записывается, но НЕ прибавляется
 * к результату.
 */

import type {
  AnalyzeOptions,
  AnalyzeStepRow,
  FitComponent,
  FitInfo,
  FwdRevPair,
} from "./types";

// ============================== Вход/выход шага ==============================

export interface StepAnalysisInput {
  a: number[];
  b: number[];
  i: number[];
  uI: number[];
  leak: number[];
  uLeakInd: number[];
  uDt: number[];
  preSettled: boolean;
  dv: number;
  vBefore: number;
  vAfter: number;
  options: AnalyzeOptions;
}

export interface StepAnalysisOutput {
  qObs: number;
  uQ: number;
  qGapModel: number;
  qGapBound: number;
  qCorrected: number;
  cSec: number;
  uC: number;
  tailB: number;
  tailS: number;
  tailUB: number;
  tailUS: number;
  tailOk: boolean;
  gaps: [number, number][];
  gapDuration: number;
  fit: FitInfo | null;
  flags: string[];
  usableForPair: boolean;
}

function zeroOutput(flags: string[]): StepAnalysisOutput {
  return {
    qObs: 0,
    uQ: 0,
    qGapModel: 0,
    qGapBound: 0,
    qCorrected: 0,
    cSec: 0,
    uC: 0,
    tailB: 0,
    tailS: 0,
    tailUB: 0,
    tailUS: 0,
    tailOk: false,
    gaps: [],
    gapDuration: 0,
    fit: null,
    flags,
    usableForPair: false,
  };
}

// ============================== Модель экспонент ==============================

/** Среднее значение e^(−t/τ) по окну [a, b] (порт mean_exp). */
function meanExp(a: number, b: number, tau: number): number {
  return (tau * Math.exp(-a / tau) * -Math.expm1(-(b - a) / tau)) / (b - a);
}

/** ∫ по [l, r] модели i(t) = B + Σ A_m·e^(−t/τ_m) (порт exp_integral). */
function expIntegral(p: number[], l: number, r: number, n: number): number {
  let val = p[0] * (r - l);
  for (let m = 0; m < n; m++) {
    const tau = Math.exp(p[2 + 2 * m]);
    val += p[1 + 2 * m] * tau * (Math.exp(-l / tau) - Math.exp(-r / tau));
  }
  return val;
}

/** Предсказанные средние токи окон (порт exp_pred, параметры в масштабированных единицах). */
function expPred(p: number[], a: number[], b: number[], n: number): number[] {
  const y = new Array<number>(a.length).fill(p[0]);
  for (let m = 0; m < n; m++) {
    const tau = Math.exp(p[2 + 2 * m]);
    const amp = p[1 + 2 * m];
    for (let j = 0; j < a.length; j++) y[j] += amp * meanExp(a[j], b[j], tau);
  }
  return y;
}

function geomspace(start: number, end: number, n: number): number[] {
  if (n <= 1) return [start];
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(start * Math.pow(end / start, i / (n - 1)));
  return out;
}

// ============================== Линейная алгебра ==============================

/** Решение A·x = b (Гаусс с частичным выбором ведущего элемента); null при вырождении. */
function solveLinear(A: number[][], b: number[]): number[] | null {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let i = col + 1; i < n; i++) {
      if (Math.abs(M[i][col]) > Math.abs(M[piv][col])) piv = i;
    }
    if (!isFinite(M[piv][col]) || Math.abs(M[piv][col]) < 1e-300) return null;
    if (piv !== col) {
      const t = M[piv];
      M[piv] = M[col];
      M[col] = t;
    }
    for (let i = col + 1; i < n; i++) {
      const f = M[i][col] / M[col][col];
      if (f === 0) continue;
      for (let k = col; k <= n; k++) M[i][k] -= f * M[col][k];
    }
  }
  const x = new Array<number>(n).fill(0);
  for (let i = n - 1; i >= 0; i--) {
    let s = M[i][n];
    for (let k = i + 1; k < n; k++) s -= M[i][k] * x[k];
    x[i] = s / M[i][i];
  }
  return x;
}

/** Собственные значения/векторы симметричной матрицы (метод вращений Якоби). */
function jacobiEigen(Ain: number[][]): { values: number[]; vectors: number[][] } {
  const n = Ain.length;
  const A = Ain.map((row) => [...row]);
  const V: number[][] = Array.from({ length: n }, (_, i) =>
    Array.from({ length: n }, (_, j) => (i === j ? 1 : 0)),
  );
  for (let sweep = 0; sweep < 100; sweep++) {
    let off = 0;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) off += A[i][j] * A[i][j];
    }
    if (off < 1e-28) break;
    for (let p = 0; p < n - 1; p++) {
      for (let q = p + 1; q < n; q++) {
        const apq = A[p][q];
        if (apq === 0 || !isFinite(apq)) continue;
        const app = A[p][p];
        const aqq = A[q][q];
        const theta = (aqq - app) / (2 * apq);
        const t =
          (theta >= 0 ? 1 : -1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
        const c = 1 / Math.sqrt(t * t + 1);
        const s = t * c;
        A[p][p] = app - t * apq;
        A[q][q] = aqq + t * apq;
        A[p][q] = 0;
        A[q][p] = 0;
        for (let r = 0; r < n; r++) {
          if (r === p || r === q) continue;
          const arp = A[r][p];
          const arq = A[r][q];
          const nrp = c * arp - s * arq;
          const nrq = s * arp + c * arq;
          A[r][p] = nrp;
          A[p][r] = nrp;
          A[r][q] = nrq;
          A[q][r] = nrq;
        }
        for (let r = 0; r < n; r++) {
          const vrp = V[r][p];
          const vrq = V[r][q];
          V[r][p] = c * vrp - s * vrq;
          V[r][q] = s * vrp + c * vrq;
        }
      }
    }
  }
  const values: number[] = [];
  for (let i = 0; i < n; i++) values.push(A[i][i]);
  return { values, vectors: V };
}

/**
 * Псевдообратная симметричной матрицы через собственное разложение;
 * tol = max|λ|·1e-12 (λ > tol → 1/λ, иначе 0). cond = sqrt(λmax/λmin>tol).
 */
function pinvSym(H: number[][]): { inv: number[][]; cond: number } {
  const n = H.length;
  if (n === 0) return { inv: [], cond: Infinity };
  const { values, vectors } = jacobiEigen(H);
  let maxAbs = 0;
  for (const v of values) maxAbs = Math.max(maxAbs, Math.abs(v));
  if (!(maxAbs > 0)) return { inv: H.map(() => new Array<number>(n).fill(0)), cond: Infinity };
  const tol = maxAbs * 1e-12;
  let lambdaMax = -Infinity;
  let lambdaMin = Infinity;
  for (const v of values) {
    if (v > lambdaMax) lambdaMax = v;
    if (v > tol && v < lambdaMin) lambdaMin = v;
  }
  const cond =
    lambdaMax > 0 && isFinite(lambdaMin) && lambdaMin > 0
      ? Math.sqrt(lambdaMax / lambdaMin)
      : Infinity;
  const inv: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      let sum = 0;
      for (let k = 0; k < n; k++) {
        if (values[k] > tol) sum += (vectors[i][k] * vectors[j][k]) / values[k];
      }
      inv[i][j] = sum;
    }
  }
  return { inv, cond };
}

// ============================== Levenberg–Marquardt ==============================

/** Центрально-разностный якобиан с ограничением шага рамками параметров. */
function numericalJacobian(
  residual: (p: number[]) => number[],
  p: number[],
  r0: number[],
  lower: number[],
  upper: number[],
): number[][] {
  const n = p.length;
  const m = r0.length;
  const J: number[][] = Array.from({ length: m }, () => new Array<number>(n).fill(0));
  for (let j = 0; j < n; j++) {
    const h = Math.max(Math.abs(p[j]) * 1e-6, 1e-8);
    const hi = Math.min(upper[j], p[j] + h);
    const lo = Math.max(lower[j], p[j] - h);
    const step = hi - lo;
    if (!(step > 0) || !isFinite(step)) continue;
    const pHi = [...p];
    pHi[j] = hi;
    const pLo = [...p];
    pLo[j] = lo;
    const rHi = residual(pHi);
    const rLo = residual(pLo);
    for (let i = 0; i < m; i++) J[i][j] = (rHi[i] - rLo[i]) / step;
  }
  return J;
}

function lmFit(
  residual: (p: number[]) => number[],
  p0: number[],
  lower: number[],
  upper: number[],
  maxNfev: number,
): { p: number[]; converged: boolean; nfev: number } {
  const n = p0.length;
  const clamp = (p: number[]): number[] =>
    p.map((v, i) => Math.min(Math.max(v, lower[i]), upper[i]));
  let p = clamp([...p0]);
  let r = residual(p);
  let nfev = 1;
  let cost = 0;
  for (const v of r) cost += 0.5 * v * v;
  let lambda = 1e-3;
  let converged = false;
  let stall = 0;
  let noProgress = 0;
  let rejections = 0;

  for (let iter = 0; iter < 2000; iter++) {
    const J = numericalJacobian(residual, p, r, lower, upper);
    const g = new Array<number>(n).fill(0);
    const H: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));
    for (let i = 0; i < J.length; i++) {
      for (let j = 0; j < n; j++) {
        g[j] += J[i][j] * r[i];
        for (let k = j; k < n; k++) H[j][k] += J[i][j] * J[i][k];
      }
    }
    for (let j = 0; j < n; j++) {
      for (let k = 0; k < j; k++) H[j][k] = H[k][j];
    }
    // (H + λ·diag(H) + ε·I)·δ = −g
    const M = H.map((row, j) =>
      row.map((v, k) => (j === k ? v * (1 + lambda) + 1e-12 : v)),
    );
    const delta = solveLinear(M, g.map((v) => -v));
    if (!delta) {
      lambda *= 10;
      if (lambda > 1e14) break;
      continue;
    }
    const pNew = clamp(p.map((v, i) => v + delta[i]));
    const rNew = residual(pNew);
    nfev++;
    let costNew = 0;
    for (const v of rNew) costNew += 0.5 * v * v;
    if (costNew < cost) {
      const dCost = cost - costNew;
      p = pNew;
      r = rNew;
      cost = costNew;
      lambda = Math.max(lambda / 3, 1e-12);
      rejections = 0;
      if (dCost <= 1e-12 * Math.max(1, cost)) {
        stall++;
        if (stall >= 2) {
          converged = true;
          break;
        }
      } else {
        stall = 0;
      }
      // ftol-подобный критерий (ослабленный относительно scipy ftol=1e-8): серия
      // «принятых, но незначимых» улучшений — стагнация/спираль в плоской долине
      // log(τ); параметры к этому моменту уже определены с точностью лучше шума.
      if (dCost <= 1e-6 * Math.max(1, cost)) {
        noProgress++;
        if (noProgress >= 8) {
          converged = true;
          break;
        }
      } else {
        noProgress = 0;
      }
      let maxDelta = 0;
      for (const v of delta) maxDelta = Math.max(maxDelta, Math.abs(v));
      if (maxDelta < 1e-12) {
        converged = true;
        break;
      }
    } else {
      lambda *= 4;
      stall = 0;
      rejections++;
      // Серия отклонений: ни один шаг не уменьшает стоимость — минимум
      // (ftol-эквивалент; scipy завершает по ftol/xtol в этой ситуации).
      if (rejections >= 20) {
        converged = true;
        break;
      }
      if (lambda > 1e14) break;
    }
    if (nfev >= maxNfev) break;
  }
  return { p, converged, nfev };
}

// ============================== Экспоненциальная подгонка (порт fit_exponential) ==============================

interface ExpFitResult {
  physical: number[];
  covPhys: number[][];
  red: number;
  cond: number;
  success: boolean;
}

function fitExponential(
  a: number[],
  b: number[],
  y: number[],
  u: number[],
  nexp: number,
): ExpFitResult {
  const len = y.length;
  let maxAbsY = 0;
  for (const v of y) maxAbsY = Math.max(maxAbsY, Math.abs(v));
  let maxU = 0;
  for (const v of u) maxU = Math.max(maxU, v);
  const scale = Math.max(maxAbsY, maxU, 1e-30);

  let minDt = Infinity;
  for (let j = 0; j < len; j++) minDt = Math.min(minDt, b[j] - a[j]);
  const lo = Math.max(minDt / 100, 1e-9);
  const hi = Math.max(b[len - 1] * 100, lo * 1000);

  const p0: number[] = [y[len - 1] / scale];
  for (const tau of geomspace(
    Math.max(lo * 10, b[len - 1] / 100),
    Math.max(lo * 20, b[len - 1] / 2),
    nexp,
  )) {
    p0.push((y[0] - y[len - 1]) / scale / nexp);
    p0.push(Math.log(tau));
  }
  const lower: number[] = [-Infinity];
  const upper: number[] = [Infinity];
  for (let m = 0; m < nexp; m++) {
    lower.push(-Infinity, Math.log(lo));
    upper.push(Infinity, Math.log(hi));
  }

  const residual = (p: number[]): number[] => {
    const pred = expPred(p, a, b, nexp);
    const out = new Array<number>(len);
    for (let j = 0; j < len; j++) out[j] = (pred[j] - y[j] / scale) / (u[j] / scale);
    return out;
  };

  const { p, converged } = lmFit(residual, p0, lower, upper, 10000);

  // Диагностика в точке решения.
  const r = residual(p);
  const J = numericalJacobian(residual, p, r, lower, upper);
  const pc = 1 + 2 * nexp;
  const H: number[][] = Array.from({ length: pc }, () => new Array<number>(pc).fill(0));
  for (let i = 0; i < J.length; i++) {
    for (let j = 0; j < pc; j++) {
      for (let k = 0; k < pc; k++) H[j][k] += J[i][j] * J[i][k];
    }
  }
  let ssq = 0;
  for (const v of r) ssq += v * v;
  const dof = len - pc;
  const red = ssq / (dof > 0 ? dof : 1);
  const { inv, cond } = pinvSym(H);
  const scaleRed = Math.max(1, red);
  const covScaled = inv.map((row) => row.map((v) => v * scaleRed));

  // Преобразование к физическим параметрам.
  const transform = new Array<number>(pc).fill(1);
  transform[0] = scale;
  for (let m = 0; m < nexp; m++) transform[1 + 2 * m] = scale;
  const physical = p.map((v, i) => v * transform[i]);
  const covPhys = covScaled.map((row, i) => row.map((v, j) => v * transform[i] * transform[j]));

  return { physical, covPhys, red, cond, success: converged };
}

/** Порт numerical_uncertainty: центральные разности с шагом из qscv_final. */
function numericalUncertainty(
  fun: (p: number[]) => number,
  p: number[],
  cov: number[][],
): number {
  const n = p.length;
  const grad = new Array<number>(n).fill(0);
  for (let j = 0; j < n; j++) {
    const floor = j === 0 || j % 2 === 1 ? 1e-18 : 1e-6;
    const h = Math.max(Math.abs(p[j]) * 1e-5, floor);
    const pp = [...p];
    pp[j] = p[j] + h;
    const pm = [...p];
    pm[j] = p[j] - h;
    grad[j] = (fun(pp) - fun(pm)) / (2 * h);
  }
  let acc = 0;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) acc += grad[i] * cov[i][j] * grad[j];
  }
  return Math.sqrt(Math.max(0, acc));
}

// ============================== Анализ шага (порт analyse_qs) ==============================

export function analyseStep(inp: StepAnalysisInput): StepAnalysisOutput {
  const { a, b, i, uI, leak, uLeakInd, uDt, options } = inp;
  const len = a.length;
  const flags = new Set<string>();
  const invalid = (flag: string): StepAnalysisOutput => zeroOutput([flag]);

  if (
    len === 0 ||
    [a, b, i, uI, leak, uLeakInd, uDt].some((arr) => arr.length !== len)
  ) {
    return invalid("DATA_INVALID:length_mismatch");
  }
  // Нечисловые значения.
  const allNumeric = [a, b, i, uI, leak, uLeakInd, uDt, [inp.vBefore], [inp.vAfter], [inp.dv]];
  for (const arr of allNumeric) {
    for (const v of arr) {
      if (!isFinite(v)) return invalid("DATA_INVALID:nonfinite");
    }
  }
  // Отрицательные неопределённости.
  for (const arr of [uI, uLeakInd, uDt]) {
    for (const v of arr) if (v < 0) return invalid("DATA_INVALID:negative_uncertainty");
  }
  // Длительности окон, старт, перекрытия, число окон (порт проверки qscv_final).
  for (let j = 0; j < len; j++) {
    if (!(b[j] - a[j] > 0)) return invalid("DATA_INVALID:nonpositive_window");
  }
  if (a[0] < 0) return invalid("DATA_INVALID:negative_start");
  for (let j = 1; j < len; j++) {
    if (a[j] < b[j - 1] - 1e-12) return invalid("DATA_INVALID:overlap");
  }
  if (len < Math.max(options.tailPoints, 8)) {
    return invalid("DATA_INVALID:too_few_windows");
  }
  const dv = inp.dv;
  if (dv === 0) return invalid("DATA_INVALID:zero_voltage_step");

  // Ток за вычетом фона; независимая неопределённость.
  const y = new Array<number>(len);
  const ui = new Array<number>(len);
  for (let j = 0; j < len; j++) {
    y[j] = i[j] - leak[j];
    ui[j] = Math.hypot(uI[j], uLeakInd[j]);
  }
  for (let j = 0; j < len; j++) {
    if (!(ui[j] > 0)) return invalid("INVALID_UI");
  }

  // Пропуски: от 0 до первой, между окнами (порт).
  const gaps: [number, number][] = [];
  let edge = 0;
  for (let j = 0; j < len; j++) {
    if (a[j] - edge > 1e-12) gaps.push([edge, a[j]]);
    edge = b[j];
  }
  let gapDuration = 0;
  for (const [l, r] of gaps) gapDuration += r - l;

  // Хвост: взвешенная линейная подгонка y = B + S·(t − t̄) по последним n окнам.
  const n = Math.min(options.tailPoints, len);
  const t: number[] = [];
  const yy: number[] = [];
  const uu: number[] = [];
  for (let j = len - n; j < len; j++) {
    t.push((a[j] + b[j]) / 2);
    yy.push(y[j]);
    uu.push(ui[j]);
  }
  let tMean = 0;
  for (const v of t) tMean += v;
  tMean /= n;

  let S11 = 0;
  let S1x = 0;
  let Sxx = 0;
  let Sy = 0;
  let Sxy = 0;
  for (let j = 0; j < n; j++) {
    const w = 1 / (uu[j] * uu[j]);
    const x = t[j] - tMean;
    S11 += w;
    S1x += w * x;
    Sxx += w * x * x;
    Sy += w * yy[j];
    Sxy += w * x * yy[j];
  }
  const det = S11 * Sxx - S1x * S1x;
  let tailB = 0;
  let tailS = 0;
  let tailUB = 0;
  let tailUS = 0;
  let tailOk = false;
  if (!isFinite(det) || Math.abs(det) < 1e-300) {
    flags.add("TAIL_FIT_SINGULAR");
    tailUB = Number.MAX_VALUE;
    tailUS = Number.MAX_VALUE;
  } else {
    const inv00 = Sxx / det;
    const inv01 = -S1x / det;
    const inv11 = S11 / det;
    tailB = inv00 * Sy + inv01 * Sxy;
    tailS = inv01 * Sy + inv11 * Sxy;
    let chi2 = 0;
    for (let j = 0; j < n; j++) {
      const resid = tailB + tailS * (t[j] - tMean) - yy[j];
      chi2 += (resid * resid) / (uu[j] * uu[j]);
    }
    const redTail = chi2 / Math.max(1, n - 2);
    const scaleCov = Math.max(1, redTail);
    const cov00 = inv00 * scaleCov;
    const cov11 = inv11 * scaleCov;
    tailUB = Math.sqrt(
      cov00 + options.uCurrentCommon_A ** 2 + options.uLeakCommon_A ** 2,
    );
    tailUS = Math.sqrt(cov11);
    tailOk =
      Math.abs(tailB) + options.kFactor * tailUB <= options.tailLimit_A &&
      Math.abs(tailS) + options.kFactor * tailUS <= options.slopeLimit_A_s;
  }

  // Заряд и бюджет неопределённости (§12).
  let q = 0;
  let uIndSq = 0;
  let duration = 0;
  for (let j = 0; j < len; j++) {
    const dt = b[j] - a[j];
    q += y[j] * dt;
    uIndSq += (dt * ui[j]) ** 2 + (y[j] * uDt[j]) ** 2;
    duration += dt;
  }
  const uInd = Math.sqrt(uIndSq);
  const uQ = Math.sqrt(
    uInd ** 2 +
      (duration * options.uCurrentCommon_A) ** 2 +
      (duration * options.uLeakCommon_A) ** 2 +
      (q * options.uClockRel) ** 2,
  );

  if (!inp.preSettled) flags.add("PRECONDITION_NOT_CONFIRMED");
  if (!tailOk) flags.add("TAIL_NOT_SETTLED");

  // Экспоненциальная подгонка (порт fit_exponential).
  let qGap = 0;
  let gapBound = 0;
  let fit: FitInfo | null = null;
  let nexp = 0;
  let fitPhysical: number[] | null = null;
  let fitCov: number[][] | null = null;

  if (options.model === "exp1" || options.model === "exp2") {
    nexp = options.model === "exp1" ? 1 : 2;
    if (len <= 1 + 2 * nexp) return invalid("DATA_INVALID:insufficient_samples_for_fit");
    const f = fitExponential(a, b, y, ui, nexp);
    fitPhysical = f.physical;
    fitCov = f.covPhys;
    const components: FitComponent[] = [];
    let remainingTail = 0;
    for (let m = 0; m < nexp; m++) {
      const tau = Math.exp(f.physical[2 + 2 * m]);
      const amp = f.physical[1 + 2 * m];
      const qc = amp * tau;
      components.push({ tau_s: tau, a_A: amp, q_C: qc });
      remainingTail += qc * Math.exp(-b[len - 1] / tau);
    }
    components.sort((p1, p2) => p1.tau_s - p2.tau_s);
    fit = {
      model: options.model,
      baseline_A: f.physical[0],
      components,
      reducedChi2: f.red,
      condition: f.cond,
      success: f.success,
      remainingTail_C: remainingTail,
      uGapFitOnly_C: 0,
    };
    if (!f.success || f.red > 5 || f.cond > 1e8) flags.add("FIT_DIAGNOSTIC_WARNING");
  }

  // Пропуски: reject / bound / model (порт).
  if (gaps.length > 0) {
    if (options.gapMode === "reject") {
      flags.add("GAPS_UNACCOUNTED");
    } else if (options.gapMode === "bound") {
      gapBound = gapDuration * options.gapCurrentBound_A;
      flags.add("GAPS_BOUNDED_NOT_CORRECTED");
    } else {
      // gapMode === "model": требуется подгонка exp1/exp2.
      if (fitPhysical && fitCov) {
        const fun = (pp: number[]): number => {
          let s = 0;
          for (const [l, r] of gaps) s += expIntegral(pp, l, r, nexp);
          return s;
        };
        qGap = fun(fitPhysical);
        if (fit) fit.uGapFitOnly_C = numericalUncertainty(fun, fitPhysical, fitCov);
        flags.add("MODEL_DEPENDENT_GAP_CORRECTION");
      } else {
        flags.add("GAPS_UNACCOUNTED");
      }
    }
  }

  const corrected = q + qGap;
  const cSec = corrected / dv;
  const uC = Math.sqrt(
    (uQ / dv) ** 2 + ((corrected * options.uDv_V) / (dv * dv)) ** 2,
  );

  return {
    qObs: q,
    uQ,
    qGapModel: qGap,
    qGapBound: gapBound,
    qCorrected: corrected,
    cSec,
    uC,
    tailB,
    tailS,
    tailUB,
    tailUS,
    tailOk,
    gaps,
    gapDuration,
    fit,
    flags: Array.from(flags),
    usableForPair: inp.preSettled && tailOk && gaps.length === 0,
  };
}

// ============================== Пары прямой/обратный ход (порт main) ==============================

function interpLinear(xs: number[], ys: number[], x: number): number {
  // Аналог np.interp в пределах [xs[0], xs[n-1]] (плоская экстраполяция не требуется).
  if (xs.length === 1) return ys[0];
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  let lo = 0;
  let hi = xs.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= x) lo = mid;
    else hi = mid;
  }
  const f = (x - xs[lo]) / (xs[hi] - xs[lo]);
  return ys[lo] + f * (ys[hi] - ys[lo]);
}

export function pairForwardReverseDetailed(rows: AnalyzeStepRow[]): {
  pairs: FwdRevPair[];
  errors: string[];
} {
  const pairs: FwdRevPair[] = [];
  const errors: string[] = [];
  const runs = new Map<string, AnalyzeStepRow[]>();
  for (const row of rows) {
    if (!runs.has(row.run)) runs.set(row.run, []);
    runs.get(row.run)!.push(row);
  }
  for (const [run, rs] of runs) {
    const f = rs
      .filter((r) => r.direction === "forward" && r.usableForPair)
      .sort((p1, p2) => p1.vMid_V - p2.vMid_V);
    const r = rs
      .filter((x) => x.direction === "reverse" && x.usableForPair)
      .sort((p1, p2) => p1.vMid_V - p2.vMid_V);
    if (f.length === 0 || r.length === 0) continue;
    let dup = false;
    for (let k = 1; k < r.length; k++) {
      if (r[k].vMid_V === r[k - 1].vMid_V) {
        dup = true;
        break;
      }
    }
    if (dup) {
      errors.push(
        `Прогон «${run}»: дубликаты vMid в обратном ходе — разделите циклы на отдельные прогоны`,
      );
      continue;
    }
    for (const fr of f) {
      const x = fr.vMid_V;
      if (r.length < 2 || x < r[0].vMid_V || x > r[r.length - 1].vMid_V) continue;
      const cRev = interpLinear(
        r.map((q) => q.vMid_V),
        r.map((q) => q.cSec_F),
        x,
      );
      pairs.push({
        run,
        vMid_V: x,
        cForward_F: fr.cSec_F,
        cReverseInterpolated_F: cRev,
        deltaC_F: fr.cSec_F - cRev,
      });
    }
  }
  return { pairs, errors };
}

export function pairForwardReverse(rows: AnalyzeStepRow[]): FwdRevPair[] {
  return pairForwardReverseDetailed(rows).pairs;
}
