/**
 * QSCV B2910BL — общий контракт типов.
 *
 * Основание: «Методика квазистатических измерений на Keysight B2910BL» (§ указаны
 * в комментариях), qscv_final.py (офлайн-анализатор), physical_observer.py
 * (пассивная модель C0 || G || R1C1), quasi_static_cv_B2910BL.md (SCPI),
 * B2900B/BL Programming Guide / User's Guide (проверенные команды и параметры).
 *
 * Все величины в СИ: В, А, Кл (C), Ф, с, Ом, См, Дж.
 */

// ============================== Паспорт прибора (§4) ==============================

export type Wiring = "2w" | "4w";

export interface InstrumentPassport {
  id?: string;
  model: string; // модель, по умолчанию B2910BL
  serialNumber: string;
  firmware: string;
  calibrationDate: string; // ISO
  temperatureC: number | null;
  warmupMinutes: number; // прогрев до self-cal (User's Guide: 60 минут)
  selfCalDone: boolean; // *CAL? выполнена
  selfTestDone: boolean; // *TST? выполнен
  wiring: Wiring; // 4w: :SENS:REM ON — Force/Sense HI→затвор, LO→подложка
  notes: string; // кабели, экранирование, guard, воспроизводимое положение образца
}

export const DEFAULT_INSTRUMENT: InstrumentPassport = {
  model: "B2910BL",
  serialNumber: "",
  firmware: "",
  calibrationDate: "",
  temperatureC: 23.0,
  warmupMinutes: 60,
  selfCalDone: false,
  selfTestDone: false,
  wiring: "4w",
  notes: "",
};

// ============================== Модель DUT (симуляция) ==============================
// Пассивная линейная модель PHYSICAL_OBSERVER: параллельные ветви
//   C0 (быстрая, Q_fast = C0·V)  ||  G (утечка, i = G·V)
//   || (R1 последовательно с C1): tau = R1·C1, x — напряжение на C1, ẋ = (v−x)/τ
// Расширение до двух релаксационных ветвей — как в демо qscv_final.py
// (1 нФ τ=3 мс + 0.2 нФ τ=20 мс). Нелинейная Q_fast(V) — только помеченная
// демонстрационная модель (сигмоид C(V)), методика §3: C_qs = dQ_eq/dV.

export interface DutBranch {
  c_F: number; // ёмкость ветви, Ф
  tau_s: number; // tau = R·C, с; R = tau/C
}

export type DutKind = "linear" | "mos_demo" | "ref_c" | "ref_rc";

export interface DutModel {
  kind: DutKind;
  label: string;
  // Линейная часть (linear / mos_demo / ref_*)
  c0_F: number; // быстрая ёмкость (linear)
  g_S: number; // проводимость утечки (i_leak = G·V)
  branches: DutBranch[]; // релаксационные ветви (0–2 шт.)
  // Нелинейная Q_fast(V) — только kind = "mos_demo":
  // C_fast(V) = cMin + (cOx − cMin)·σ((vfb − V)/width), σ — логистическая;
  // Q_fast(V) = cMin·V − (cOx − cMin)·width·softplus((vfb − V)/width) + const
  cOx_F: number;
  cMin_F: number;
  vfb_V: number;
  width_V: number;
}

export const DUT_PRESETS: Record<string, DutModel> = {
  demo_qscv: {
    kind: "linear",
    label: "Демо qscv_final: 1 нФ (τ=3 мс) + 0.2 нФ (τ=20 мс) + утечка",
    c0_F: 0,
    g_S: 2e-11,
    branches: [
      { c_F: 1e-9, tau_s: 0.003 },
      { c_F: 0.2e-9, tau_s: 0.02 },
    ],
    cOx_F: 0,
    cMin_F: 0,
    vfb_V: 0,
    width_V: 0,
  },
  observer_demo: {
    kind: "linear",
    label: "Демо physical_observer: C0=1 нФ, G=0.1 нС, C1=0.3 нФ, τ=12 мс",
    c0_F: 1e-9,
    g_S: 1e-10,
    branches: [{ c_F: 3e-10, tau_s: 0.012 }],
    cOx_F: 0,
    cMin_F: 0,
    vfb_V: 0,
    width_V: 0,
  },
  mos_demo: {
    kind: "mos_demo",
    label: "MOS-демо (нелинейная Q_fast + ловушки + утечка) — демонстрационная модель",
    c0_F: 0,
    g_S: 1e-11,
    branches: [{ c_F: 1.5e-10, tau_s: 0.02 }],
    cOx_F: 1e-9,
    cMin_F: 2e-10,
    vfb_V: 0.05,
    width_V: 0.03,
  },
  ref_c: {
    kind: "ref_c",
    label: "Эталонный конденсатор 1 нФ (проверка §6)",
    c0_F: 1e-9,
    g_S: 0,
    branches: [],
    cOx_F: 0,
    cMin_F: 0,
    vfb_V: 0,
    width_V: 0,
  },
  ref_rc: {
    kind: "ref_rc",
    label: "Эталон RC: 1 нФ, τ=1 мс (R=1 МОм) — проверка §6",
    c0_F: 0,
    g_S: 0,
    branches: [{ c_F: 1e-9, tau_s: 1e-3 }],
    cOx_F: 0,
    cMin_F: 0,
    vfb_V: 0,
    width_V: 0,
  },
};

// ============================== Паспорт программы (§5) ==============================

export type Protocol = "kuhn_staircase" | "aperture_charge";
export type Direction = "forward" | "reverse";
export type SpeedMode = "auto" | "short" | "medium" | "normal" | "long" | "manual";
export type BackgroundMode = "none" | "blank" | "mean_tail" | "linear_tail";
export type GapMode = "reject" | "bound" | "model";
export type FitModel = "none" | "exp1" | "exp2";

export interface MeasurementConfig {
  name: string;
  protocol: Protocol;

  // --- Напряжение (§5): начальное, конечное, полярность, размер шага
  vStart_V: number;
  vEnd_V: number;
  vStep_V: number; // положительное число; направление задаётся vStart→vEnd
  directions: Direction[]; // ["forward"], ["forward","reverse"]
  cycles: number; // повторов цикла (§7.3 п.3)

  // --- Ток (§5): compliance, диапазон, autorange
  iCompliance_A: number;
  iRangeMode: "auto" | "fixed";
  iRange_A: number; // 10e-9 … 100e-3 (измерительные диапазоны B2900B/BL)

  // --- Интегрирование (§5): aperture либо NPLC и фактическая частота сети
  speedMode: SpeedMode; // AUTO/SHORT/MEDIUM/NORMAL/LONG/MANUAL (User's Guide)
  apertureMode: "nplc" | "seconds";
  nplc: number; // число периодов сети (manual + nplc)
  aperture_s: number; // длительность апертуры, с (manual + seconds)
  lineFreq_Hz: number; // фактическая частота сети (50/60)

  // --- Тайминг (§5): триггерная модель B2900 (Programming Guide, Fig. 1-2):
  //   :TRIG:SOUR TIM — источник триггера TIMER
  //   :TRIG:TIM      — интервал между точками триггера
  //   :TRIG:COUN     — счёт триггеров (протокол B: апертур на шаг)
  //   :TRIG:TRAN:DEL — задержка изменения источника от триггера
  //   :TRIG:ACQ:DEL  — задержка измерения от триггера
  trigInterval_s: number; // период таймера ≠ апертура (§6)
  trigCount: number; // апертур на шаг (протокол B)
  tranDelay_s: number; // задержка источника
  acqDelay_s: number; // задержка первой апертуры от триггера
  tranTime_s: number; // длительность перехода источника (модель линейного фронта)

  // --- Подготовка (§5, §7.1): предварительный bias, длительность, критерий
  preBias_V: number;
  preBiasDuration_s: number;
  settleDIdt_A_s: number; // критерий |dI/dt| для установления, А/с (0 = только время)

  // --- Регистрация (§5): границы окон вычисляются и логируются (a_j, b_j)

  // --- Фон (§5): способ оценки утечки и blank
  backgroundMode: BackgroundMode;

  // --- Допуск (§5): критерии хвоста, сходимости, повторяемости (§9)
  tailPoints: number; // N последних окон для анализа хвоста
  tailLimit_A: number; // I_limit: |B| + k·u(B) ≤ I_limit
  slopeLimit_A_s: number; // S_limit: |S| + k·u(S) ≤ S_limit
  kFactor: number; // k (1.96 — приближённая нормальная интерпретация)
  convergencePct: number; // допуск сходимости заряда при удлинении записи, %

  // --- Пропуски и модель (qscv_final: --gap-mode, --model)
  gapMode: GapMode;
  gapCurrentBound_A: number; // для bound: |Qgap| ≤ Ibound·Tgap
  fitModel: FitModel;
  stopOnTailFail: boolean; // останавливать последовательность при непрошедшем хвосте

  // --- Неопределённость (§12, qscv_final)
  uCurrentCommon_A: number; // общий токовый член (offset)
  uLeakCommon_A: number; // общий член фона
  uClockRel: number; // относительная неопределённость длительности
  uDv_V: number; // неопределённость ΔV_DUT

  // --- Служебные циклы (§5): autozero, фильтры
  autozero: boolean; // сверять по SCPI Reference; в симуляции влияет на шум
  outputFilter: boolean; // :OUTP:FILT

  // --- Шумовая модель SMU (симуляция): u_i = uIOffset + uIGainRel·|I| + offset
  uIOffset_A: number; // белый шум, А (на апертуру)
  uIGainRel: number; // относительный шум
  iOffset_A: number; // систематический offset прибора (ловится blank-ом)
}

export const DEFAULT_CONFIG: MeasurementConfig = {
  name: "Зарядовый протокол — демо qscv_final",
  protocol: "aperture_charge",
  vStart_V: 0,
  vEnd_V: 0.1,
  vStep_V: 0.02,
  directions: ["forward", "reverse"],
  cycles: 1,
  iCompliance_A: 1e-6,
  iRangeMode: "auto",
  iRange_A: 10e-9,
  speedMode: "manual",
  apertureMode: "seconds",
  nplc: 1,
  aperture_s: 0.0008,
  lineFreq_Hz: 50,
  trigInterval_s: 0.001,
  trigCount: 100,
  tranDelay_s: 0,
  acqDelay_s: 0,
  tranTime_s: 0.000001,
  preBias_V: 0,
  preBiasDuration_s: 0.3,
  settleDIdt_A_s: 0,
  backgroundMode: "none",
  tailPoints: 10,
  tailLimit_A: 1e-12,
  slopeLimit_A_s: 1e-10,
  kFactor: 1.96,
  convergencePct: 1,
  gapMode: "reject",
  gapCurrentBound_A: 1e-11,
  fitModel: "exp2",
  stopOnTailFail: false,
  uCurrentCommon_A: 0,
  uLeakCommon_A: 0,
  uClockRel: 0,
  uDv_V: 0,
  autozero: true,
  outputFilter: false,
  uIOffset_A: 2e-13,
  uIGainRel: 0,
  iOffset_A: 0,
};

// ============================== Данные апертур (§13) ==============================

export interface ApertureRecord {
  j: number; // индекс апертуры, 1..N
  a_s: number; // начало окна, с (от триггера перехода)
  b_s: number; // конец окна, с
  i_avg_A: number; // измеренный средний ток (Ī_j)
  i_background_A: number; // фон L_j
  u_i_A: number; // неопределённость тока
  status: string; // OK | OVLD | COMP | ...
}

// ============================== Результаты анализа шага ==============================

export interface FitComponent {
  tau_s: number;
  a_A: number; // амплитуда A_m
  q_C: number; // компонентный заряд A·τ (§9: не доказывает механизм ловушек)
}

export interface FitInfo {
  model: "exp1" | "exp2";
  baseline_A: number; // B подгонки
  components: FitComponent[]; // сортировка по tau
  reducedChi2: number;
  condition: number; // обусловленность якобиана
  success: boolean;
  remainingTail_C: number; // Σ A·τ·e^(−b_N/τ) — оставшийся хвост (не прибавляется)
  uGapFitOnly_C: number; // u коррекции пропусков только от fit
}

export interface TruthInfo {
  // Только для симуляции: истинное разложение заряда перехода (§13: «истина»)
  qFast_C: number; // быстрая ветвь (C0 / нелинейная Q_fast)
  qBranch_C: number[]; // заряды релаксационных ветвей за окно наблюдения
  qLeak_C: number; // интеграл утечки за окно
  qRegistered_C: number; // истинный заряд внутри окон [a_j,b_j]
  qInGaps_C: number; // истинный заряд в пропусках (до первой, между, после)
  qTotal_C: number; // полный заряд перехода до бесконечности
  cSecTrue_F: number; // истинная секущая ΔQ_eq/ΔV
}

export interface StepResult {
  step: number;
  direction: Direction;
  vBefore_V: number;
  vAfter_V: number;
  vMid_V: number;
  dv_V: number;
  preSettled: boolean; // §7.1: первый шаг ветви = false без отдельной подготовки
  apertures: ApertureRecord[];
  gaps_s: [number, number][]; // промежутки [l, r] (от 0 до первой, между, после последней)
  gapDuration_s: number;

  // Непосредственно измеренное / рассчитанное из данных (§13)
  qObs_C: number; // ΔQ_obs = Σ (Ī_j − L_j)(b_j − a_j)
  uQ_C: number; // u(Q_obs) по §12
  // Восстановленное по модели (§8: Q_initial_gap + Q_inter_gaps + Q_tail)
  qGapModel_C: number;
  qGapBound_C: number;
  qCorrected_C: number; // ΔQ = ΔQ_obs + коррекция
  cSec_F: number; // C_sec = ΔQ/ΔV_DUT
  uC_F: number;

  // Хвост (§9): i = B + S·(t − t_mean)
  tailB_A: number;
  tailS_A_s: number;
  tailUB_A: number;
  tailUS_A_s: number;
  tailOk: boolean;

  fit: FitInfo | null;

  // Энергия (§10, PHYSICAL_OBSERVER §8)
  wPort_J: number; // ∫V·I dt за шаг (истина модели)
  dU_J: number; // изменение запасённой энергии
  dissipated_J: number; // диссипация модели (Gv² + Σ(C/τ)(v−x)²)
  energyResidual_J: number; // контроль баланса (должен быть ~0)

  flags: string[]; // PRECONDITION_NOT_CONFIRMED, TAIL_NOT_SETTLED, GAPS_UNACCOUNTED, ...
  usableForPair: boolean;

  // Протокол A (Кун): одиночное измерение на ступень
  kuhn?: {
    iMeas_A: number;
    rampRate_Vps: number; // r = ΔV/T
    cKuhn_F: number; // |Ī/r|
    cKuhnLeakCorrected_F: number; // |(Ī − L)/r|
    apertureStart_s: number; // положение апертуры в ступени
  };

  truth: TruthInfo | null;
}

// ============================== Прогон ==============================

export interface CvPoint {
  vMid_V: number;
  cSec_F: number;
  uC_F: number;
  cSecTrue_F: number | null;
  direction: Direction;
  usable: boolean;
}

export interface RunResult {
  id: string;
  label: string;
  direction: Direction;
  cycle: number;
  protocol: Protocol;
  config: MeasurementConfig;
  dut: DutModel;
  steps: StepResult[];
  stopped: boolean;
  stopReason: string | null;

  cvCurve: CvPoint[];
  // Энергия за прогон (§10)
  wPortTotal_J: number;
  dUTotal_J: number;
  dissipatedTotal_J: number;
  loopArea_J: number; // ∮V dQ по циклу fwd+rev (гистерезисные потери)

  // Фактический тайминг (§6: реальные границы окон)
  timing: {
    tranDelay_s: number;
    acqDelay_s: number;
    tranTime_s: number;
    interval_s: number;
    aperture_s: number;
    firstA_s: number;
    firstB_s: number;
    lastB_s: number;
    initialGap_s: number; // от начала перехода до первой апертуры
    deadTime_s: number; // интервал − апертура
  };

  createdAt: string;
}

// ============================== Верификация (§6) ==============================

export interface VerificationRequest {
  refType: "c" | "rc";
  cRef_F: number;
  rRef_ohm: number; // для rc: tau = R·C
  dv_V: number;
  timing: {
    interval_s: number;
    aperture_s: number;
    tranDelay_s: number;
    acqDelay_s: number;
    tranTime_s: number;
    count: number;
  };
  tolerancePct: number; // допуск восстановления C·ΔV, %
  noise_A: number;
}

export interface VerificationCheck {
  label: string;
  value: string;
  ok: boolean | null; // null = информационное
  note?: string;
}

export interface VerificationResult {
  apertures: ApertureRecord[];
  qExpected_C: number; // C·ΔV
  qRecovered_C: number; // ΔQ_obs
  qRegisteredTrue_C: number; // истина в окнах
  qInGaps_C: number; // истина в пропусках
  errorRelPct: number;
  tolerancePct: number;
  pass: boolean;
  checks: VerificationCheck[];
  timing: RunResult["timing"];
}

// ============================== Офлайн-анализ (порт qscv_final) ==============================

export interface AcquisitionRow {
  run: string;
  direction: string;
  step: number;
  v_before_V: number;
  v_after_V: number;
  a_s: number;
  b_s: number;
  i_A: number;
  u_i_A: number;
  leak_A: number;
  u_leak_ind_A: number;
  u_dt_s: number;
  pre_settled: boolean;
}

export interface AnalyzeOptions {
  model: FitModel;
  gapMode: GapMode;
  gapCurrentBound_A: number;
  tailPoints: number;
  tailLimit_A: number;
  slopeLimit_A_s: number;
  kFactor: number;
  uCurrentCommon_A: number;
  uLeakCommon_A: number;
  uClockRel: number;
  uDv_V: number;
}

export interface AnalyzeStepRow {
  run: string;
  direction: string;
  step: number;
  vMid_V: number;
  qObserved_C: number;
  uQ_C: number;
  qGapModel_C: number;
  qGapBound_C: number;
  qCorrected_C: number;
  cSec_F: number;
  uC_F: number;
  tailCurrent_A: number;
  tailSlope_A_s: number;
  tailSettled: boolean;
  preSettled: boolean;
  gapDuration_s: number;
  usableForPair: boolean;
  flags: string[];
  fit: FitInfo | null;
  gaps_s: [number, number][];
}

export interface FwdRevPair {
  run: string;
  vMid_V: number;
  cForward_F: number;
  cReverseInterpolated_F: number;
  deltaC_F: number;
}

export interface AnalyzeResult {
  steps: AnalyzeStepRow[];
  pairs: FwdRevPair[];
  errors: string[];
}

// ============================== SCPI ==============================

export interface ScpiRequest {
  config: MeasurementConfig;
  protocol: Protocol;
  direction: Direction;
  includeReverse: boolean;
}

export interface ScpiResult {
  script: string; // полный скрипт с комментариями
  commands: { cmd: string; comment: string }[];
  notes: string[]; // предупреждения о сверке с SCPI Reference
}

// ============================== API ==============================

export interface RunSummary {
  id: string;
  label: string;
  protocol: string;
  direction: string;
  cycle: number;
  stepsCount: number;
  stopped: boolean;
  stopReason: string | null;
  createdAt: string;
}

export type JournalFormat = "methodology" | "qscv_final";

// ============================== Прочее ==============================

export const STOP_REASONS = [
  "BREAKDOWN_OR_LEAKAGE_GROWTH", // пробой/рост утечки
  "OVERLOAD", // перегрузка диапазона
  "COMPLIANCE", // недопустимый compliance
  "INSTABILITY", // неустойчивость
  "TIMING_LOSS", // потеря временной привязки
  "TAIL_NOT_SETTLED", // хвост не прошёл допуск (если stopOnTailFail)
] as const;

export const FLAG_DESCRIPTIONS: Record<string, string> = {
  PRECONDITION_NOT_CONFIRMED: "§7.1: начальное состояние не подтверждено отдельной подготовкой",
  TAIL_NOT_SETTLED: "§9: критерий хвоста |B|+k·u(B)≤I_lim или |S|+k·u(S)≤S_lim не выполнен",
  GAPS_UNACCOUNTED: "пропуски между окнами не учтены (gap-mode reject)",
  GAPS_BOUNDED_NOT_CORRECTED: "пропуски оценены границей, но не исправлены (bound)",
  MODEL_DEPENDENT_GAP_CORRECTION: "коррекция пропусков по экспоненциальной модели — модельно зависима",
  FIT_DIAGNOSTIC_WARNING: "диагностика подгонки: reduced chi² > 5 или обусловленность > 1e8",
  COMPLIANCE_EXCEEDED: "ток превысил compliance",
  RANGE_OVERLOAD: "перегрузка диапазона измерения тока",
  MISSED_START: "§6: начальный импульс мог пройти до первой апертуры (потеря привязки)",
  INITIAL_GAP_PRESENT: "ненулевая задержка до первой апертуры",
  FIRST_STEP_DIAGNOSTIC: "§7.1: первый шаг — только диагностический",
};
