/**
 * Физическая модель DUT — порт physical_observer.py (PHYSICAL_OBSERVER.md),
 * расширенный до нескольких релаксационных ветвей и нелинейной быстрой ветви.
 *
 * Модель: параллельные ветви
 *   Q_fast(V) (быстрая: линейная C0 либо нелинейная mos_demo)
 *   || G (утечка, i = G·V)
 *   || Σ_m (R_m последовательно с C_m): tau_m = R_m·C_m, x_m — напряжение на C_m,
 *        ẋ_m = (v − x_m)/tau_m, ток ветви i_m = C_m·ẋ_m.
 *
 * Точная аналитическая propagated сегмента для линейного участка v(t) = v0 + s·t
 * (physical_observer.py, trajectory): состояние, заряды, работа порта,
 * диссипация и изменение запасённой энергии — без численного интегрирования.
 *
 * Все величины СИ: В, А, Кл, Ф, с, Ом, См, Дж.
 */

import type { DutModel } from "./types";

// ============================== Состояние ==============================

/** Напряжения x_m на ёмкостях C_m релаксационных ветвей (по порядку dut.branches). */
export interface ModelState {
  x: number[];
}

/** Узел кусочно-линейного профиля напряжения (строго возрастающие t). */
export interface VoltKnot {
  t: number;
  v: number;
}

// ============================== Быстрая ветвь ==============================

/** Стабильный softplus: ln(1+e^z) без переполнения. */
function softplus(z: number): number {
  return z > 0 ? z + Math.log(1 + Math.exp(-z)) : Math.log(1 + Math.exp(z));
}

/** Логистическая сигмоида. */
function sigmoid(z: number): number {
  return 1 / (1 + Math.exp(-z));
}

/**
 * Q_fast(V): быстрый заряд ветви (Q_fast(V) = ∫ C_fast(v) dv).
 * linear / ref_*: dut.c0_F · v.
 * mos_demo: C_fast(V) = cMin + (cOx − cMin)·σ((vfb − V)/width), σ — логистическая,
 *   отсюда Q_fast(V) = cMin·V − (cOx − cMin)·width·softplus((vfb − V)/width) + const
 *   (константа опущена: значимы только разности заряда).
 */
export function fastCharge(v: number, dut: DutModel): number {
  if (dut.kind !== "mos_demo") return dut.c0_F * v;
  const w = dut.width_V;
  const z = (dut.vfb_V - v) / w;
  return dut.cMin_F * v - (dut.cOx_F - dut.cMin_F) * w * softplus(z);
}

/**
 * C_fast(V) = dQ_fast/dV по контракту types.ts:
 * mos_demo: cMin + (cOx − cMin)·sigmoid((vfb − v)/width); иначе c0_F.
 */
export function fastCapacitance(v: number, dut: DutModel): number {
  if (dut.kind !== "mos_demo") return dut.c0_F;
  const z = (dut.vfb_V - v) / dut.width_V;
  return dut.cMin_F + (dut.cOx_F - dut.cMin_F) * sigmoid(z);
}

/**
 * Q_eq(V) — полностью релаксированное состояние: Q_fast(V) + Σ_m c_m·V.
 * Методика §3: C_qs = dQ_eq/dV.
 */
export function equilibriumCharge(v: number, dut: DutModel): number {
  let q = fastCharge(v, dut);
  for (const br of dut.branches) q += br.c_F * v;
  return q;
}

// ============================== U_fast: запасённая энергия быстрой ветви ==============================

/**
 * Ufast(V) = ∫₀^V v'·C_fast(v') dv' — для mos_demo: кумулятивная трапеция
 * по сетке 20000 точек на [−(макс|V|+1), +(макс|V|+1)] с линейной интерполяцией,
 * кэш по dut (сетка расширяется при выходе запрошенного напряжения за границы).
 * Для линейных моделей Ufast(V) = ½·c0·V² точно.
 */
interface UfastGrid {
  halfWidth: number;
  cum: number[]; // кумулятивный интеграл от −halfWidth
}

const UFAST_POINTS = 20000;
const ufastCache = new WeakMap<DutModel, UfastGrid>();

function buildUfastGrid(dut: DutModel, halfWidth: number): UfastGrid {
  const h = (2 * halfWidth) / UFAST_POINTS;
  const cum = new Array<number>(UFAST_POINTS + 1);
  cum[0] = 0;
  let prev = -halfWidth * fastCapacitance(-halfWidth, dut);
  for (let i = 1; i <= UFAST_POINTS; i++) {
    const x = -halfWidth + i * h;
    const f = x * fastCapacitance(x, dut);
    cum[i] = cum[i - 1] + ((prev + f) / 2) * h;
    prev = f;
  }
  return { halfWidth, cum };
}

function ufastInterp(entry: UfastGrid, v: number): number {
  const h = (2 * entry.halfWidth) / UFAST_POINTS;
  const pos = (v + entry.halfWidth) / h;
  const i = Math.max(0, Math.min(UFAST_POINTS - 1, Math.floor(pos)));
  const frac = pos - i;
  return entry.cum[i] + (entry.cum[i + 1] - entry.cum[i]) * frac;
}

/** Запасённая энергия быстрой ветви: ½·c0·v² (линейные) или Ufast(v) (mos_demo). */
function fastStoredEnergy(v: number, dut: DutModel): number {
  if (dut.kind !== "mos_demo") return 0.5 * dut.c0_F * v * v;
  let entry = ufastCache.get(dut);
  if (!entry || Math.abs(v) > entry.halfWidth) {
    entry = buildUfastGrid(dut, Math.abs(v) + 1);
    ufastCache.set(dut, entry);
  }
  return ufastInterp(entry, v) - ufastInterp(entry, 0);
}

// ============================== Точное распространение по сегменту ==============================

export interface SegmentResult {
  xNew: number[]; // состояние ветвей в конце сегмента
  qFast: number; // заряд быстрой ветви на сегменте: Q_fast(v1) − Q_fast(v0)
  qBranch: number[]; // заряды ветвей: C_m·(x_m' − x_m)
  qLeak: number; // заряд утечки: G·∫v dt
  wPort: number; // работа порта на сегменте (см. методику §10)
  dissipated: number; // диссипация: G·∫v² dt + Σ потери ветвей
  dU: number; // изменение запасённой энергии
}

/**
 * Точное распространение состояния по ЛИНЕЙНОМУ сегменту напряжения
 * v(t) = v0 + s·t, t ∈ [0, d] (порт physical_observer.py, trajectory).
 *
 * Для каждой ветви m (C_m, tau_m, состояние x_m):
 *   z = d/tau; one = 1 − e^(−z) (через −expm1)
 *   A = (v0 − x_m)/tau − s
 *   x_m' = x_m + s·d + A·tau·one
 *   v_integral  = v0·d + s·d²/2                      (точный ∫v dt)
 *   v2_integral = v0²·d + v0·s·d² + s²·d³/3           (точный ∫v² dt)
 *   moment = tau²·(one − z·e^(−z)), z > 1e-3, иначе
 *            tau²·(z²/2 − z³/3 + z⁴/8 − z⁵/30 + z⁶/144)  (стабильный ∫t·e^(−t/tau))
 *   branch_work_m = C_m·(s·v_integral + A·(v0·tau·one + s·moment))
 *   branch_loss_m = C_m·tau·(s²·d + 2·s·A·tau·one + A²·tau·(1−e^(−2z))/2)
 *
 * wPort: линейный случай ½·c0·(v1²−v0²) + G·v2_integral + Σ branch_work;
 *        mos_demo: (Ufast(v1) − Ufast(v0)) + G·v2_integral + Σ branch_work.
 * dU: (U_fast(v1) + Σ½C_m·x_m'²) − (U_fast(v0) + Σ½C_m·x_m²).
 */
export function propagateSegment(
  state: ModelState,
  v0: number,
  s: number,
  d: number,
  dut: DutModel,
): SegmentResult {
  const v1 = v0 + s * d;
  const n = dut.branches.length;

  // Сегментные интегралы профиля (не зависят от ветви).
  const vIntegral = v0 * d + (s * d * d) / 2;
  const v2Integral = v0 * v0 * d + v0 * s * d * d + (s * s * d * d * d) / 3;
  const leakWork = dut.g_S * v2Integral;
  const leakCharge = dut.g_S * vIntegral;

  const xNew = new Array<number>(n);
  const qBranch = new Array<number>(n);
  let branchWorkSum = 0;
  let branchLossSum = 0;
  let branchDU = 0;

  for (let m = 0; m < n; m++) {
    const C = dut.branches[m].c_F;
    const tau = dut.branches[m].tau_s;
    const x = m < state.x.length ? state.x[m] : 0;
    const z = d / tau;
    const e = Math.exp(-z);
    const one = -Math.expm1(-z);
    const A = (v0 - x) / tau - s;
    const x2 = x + s * d + A * tau * one;
    let moment: number;
    if (z > 1e-3) {
      moment = tau * tau * (one - z * e);
    } else {
      const z2 = z * z;
      const z3 = z2 * z;
      const z4 = z3 * z;
      const z5 = z4 * z;
      const z6 = z5 * z;
      moment = tau * tau * (z2 / 2 - z3 / 3 + z4 / 8 - z5 / 30 + z6 / 144);
    }
    const branchWork = C * (s * vIntegral + A * (v0 * tau * one + s * moment));
    const branchLoss =
      C * tau * (s * s * d + 2 * s * A * tau * one + (A * A * tau * -Math.expm1(-2 * z)) / 2);
    xNew[m] = x2;
    qBranch[m] = C * (x2 - x);
    branchWorkSum += branchWork;
    branchLossSum += branchLoss;
    branchDU += 0.5 * C * (x2 * x2 - x * x);
  }

  const u0 = fastStoredEnergy(v0, dut);
  const u1 = fastStoredEnergy(v1, dut);
  const qFast = fastCharge(v1, dut) - fastCharge(v0, dut);

  return {
    xNew,
    qFast,
    qBranch,
    qLeak: leakCharge,
    wPort: u1 - u0 + leakWork + branchWorkSum,
    dissipated: leakWork + branchLossSum,
    dU: u1 - u0 + branchDU,
  };
}

// ============================== Состояния: подготовка ==============================

/** Полностью релаксированное состояние при напряжении v: x_m = v. */
export function settleState(v: number, dut: DutModel): ModelState {
  return { x: dut.branches.map(() => v) };
}

/**
 * Аналитическая релаксация при постоянном vHold за время duration:
 * x_m' = vHold + (x_m − vHold)·e^(−duration/tau_m).
 */
export function relaxState(
  state: ModelState,
  vHold: number,
  duration: number,
  dut: DutModel,
): ModelState {
  return {
    x: dut.branches.map((br, m) => {
      const x = m < state.x.length ? state.x[m] : vHold;
      if (duration <= 0) return x;
      return vHold + (x - vHold) * Math.exp(-duration / br.tau_s);
    }),
  };
}

/**
 * |dI/dt| при постоянном v: Σ_m C_m·|v − x_m|/tau_m²
 * (i_m = C_m·(v − x_m)/tau_m; при v̇=0: di_m/dt = −C_m·(v − x_m)/tau_m²).
 */
export function didtAt(state: ModelState, v: number, dut: DutModel): number {
  let sum = 0;
  for (let m = 0; m < dut.branches.length; m++) {
    const br = dut.branches[m];
    const x = m < state.x.length ? state.x[m] : v;
    sum += (br.c_F * Math.abs(v - x)) / (br.tau_s * br.tau_s);
  }
  return sum;
}

// ============================== Проход по профилю ==============================

/** Диапазон накопления заряда при проходе по профилю. */
export interface WalkRange {
  l: number;
  r: number;
  qFast: number;
  qBranch: number[];
  qLeak: number;
}

export interface ProfileWalk {
  stateEnd: ModelState; // состояние в конце профиля
  wPort: number; // Σ работа порта
  dissipated: number; // Σ диссипация
  dU: number; // Σ изменение запасённой энергии
  residual: number; // wPort − dissipated − dU (контроль баланса §10)
  qFast: number; // Q_fast(v_end) − Q_fast(v_start)
  qBranch: number[]; // C_m·(x_end − x_start)
  qLeak: number; // G·∫v dt по всему профилю
}

function cloneState(s: ModelState): ModelState {
  return { x: [...s.x] };
}

/**
 * Один проход по кусочно-линейному профилю: точное распространение состояния
 * через ВСЕ сегменты (состояние непрерывно переносится через пропуски наблюдения)
 * с накоплением зарядов в заданные диапазоны [l, r] и энергетических итогов.
 * Границы диапазонов должны быть узлами профиля (каждый сегмент целиком лежит
 * внутри одного диапазона либо вне всех) — движок это гарантирует, вставляя
 * все a_j, b_j как узлы.
 */
export function walkProfile(
  knots: VoltKnot[],
  state0: ModelState,
  ranges: WalkRange[],
  dut: DutModel,
): ProfileWalk {
  let state = cloneState(state0);
  let wPort = 0;
  let dissipated = 0;
  let dU = 0;
  let qLeak = 0;

  for (let k = 0; k + 1 < knots.length; k++) {
    const t0 = knots[k].t;
    const t1 = knots[k + 1].t;
    const d = t1 - t0;
    if (d <= 0) continue;
    const v0 = knots[k].v;
    const s = (knots[k + 1].v - v0) / d;
    const res = propagateSegment(state, v0, s, d, dut);
    state = { x: res.xNew };
    wPort += res.wPort;
    dissipated += res.dissipated;
    dU += res.dU;
    qLeak += res.qLeak;
    const mid = (t0 + t1) / 2;
    for (const rg of ranges) {
      if (mid >= rg.l && mid <= rg.r) {
        rg.qFast += res.qFast;
        for (let m = 0; m < res.qBranch.length; m++) rg.qBranch[m] += res.qBranch[m];
        rg.qLeak += res.qLeak;
        break;
      }
    }
  }

  const qBranch = dut.branches.map((br, m) => {
    const x0 = m < state0.x.length ? state0.x[m] : 0;
    const x1 = m < state.x.length ? state.x[m] : 0;
    return br.c_F * (x1 - x0);
  });
  const qFast =
    knots.length > 0
      ? fastCharge(knots[knots.length - 1].v, dut) - fastCharge(knots[0].v, dut)
      : 0;

  return {
    stateEnd: state,
    wPort,
    dissipated,
    dU,
    residual: wPort - dissipated - dU,
    qFast,
    qBranch,
    qLeak,
  };
}

/** Энергетические итоги по всему профилю (§10). */
export function energyOverProfile(
  knots: VoltKnot[],
  state0: ModelState,
  dut: DutModel,
): { wPort: number; dissipated: number; dU: number; residual: number } {
  const w = walkProfile(knots, state0, [], dut);
  return { wPort: w.wPort, dissipated: w.dissipated, dU: w.dU, residual: w.residual };
}

// ============================== Заряд в окне наблюдения ==============================

export interface WindowChargeResult {
  iAvg: number; // средний ток окна (все составляющие, включая утечку)
  qFast: number;
  qBranch: number[];
  qLeak: number;
  stateAtB: ModelState; // состояние в момент b (непрерывно перенесено от knots[0].t)
}

/**
 * Истинный заряд окна [a, b] для кусочно-линейного профиля напряжения.
 * Состояние непрерывно переносится от knots[0].t до b — ветви релаксируют
 * и через пропуски наблюдения (physical_observer: «Состояние непрерывно
 * переносится через все участки и пропуски наблюдения»).
 */
export function windowCharge(
  knots: VoltKnot[],
  tStartState: ModelState,
  a: number,
  b: number,
  dut: DutModel,
): WindowChargeResult {
  if (knots.length < 2) {
    throw new Error("windowCharge: профиль напряжения должен содержать минимум 2 узла");
  }
  const tFirst = knots[0].t;
  const tLast = knots[knots.length - 1].t;
  if (!(a >= tFirst) || !(b <= tLast) || !(b > a)) {
    throw new Error(
      `windowCharge: окно [${a}, ${b}] вне профиля [${tFirst}, ${tLast}] или пусто`,
    );
  }

  let state = cloneState(tStartState);
  let qFast = 0;
  const qBranch = dut.branches.map(() => 0);
  let qLeak = 0;

  for (let k = 0; k + 1 < knots.length; k++) {
    const t0 = knots[k].t;
    const t1 = knots[k + 1].t;
    const d = t1 - t0;
    if (d <= 0) continue;
    if (t0 >= b) break;
    const v0k = knots[k].v;
    const s = (knots[k + 1].v - v0k) / d;

    // Часть сегмента до окна: [t0, min(t1, a)] — только перенос состояния.
    const preEnd = Math.min(t1, a);
    if (preEnd > t0) {
      const res = propagateSegment(state, v0k, s, preEnd - t0, dut);
      state = { x: res.xNew };
    }

    // Часть сегмента внутри окна: [max(t0,a), min(t1,b)] — перенос + накопление.
    const l = Math.max(t0, a);
    const r = Math.min(t1, b);
    if (r > l) {
      const v0sub = v0k + s * (l - t0);
      const res = propagateSegment(state, v0sub, s, r - l, dut);
      qFast += res.qFast;
      for (let m = 0; m < res.qBranch.length; m++) qBranch[m] += res.qBranch[m];
      qLeak += res.qLeak;
      state = { x: res.xNew };
    }
    if (b <= t1) break; // состояние в b достигнуто
  }

  let total = qFast + qLeak;
  for (const q of qBranch) total += q;
  return { iAvg: total / (b - a), qFast, qBranch, qLeak, stateAtB: state };
}
