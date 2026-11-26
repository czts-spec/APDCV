/**
 * Движок квазистатических измерений (методика §5–§10, §7.3):
 *  - протокол B (aperture_charge): N апертур на шаг, анализ хвоста, пропуски;
 *  - протокол A (kuhn_staircase): одиночное измерение на ступень, C = |Ī/r|;
 *  - верификация §6: восстановление известного заряда C·ΔV на эталоне.
 *
 * Глобальное состояние ModelState x_m переносится НЕПРЕРЫВНО через все шаги,
 * направления и циклы (physical_observer). Условия остановки §7.3 проверяются
 * после каждого шага; при срабатывании остальные шаги не выполняются.
 */

import type {
  ApertureRecord,
  AnalyzeOptions,
  Direction,
  DutModel,
  MeasurementConfig,
  RunResult,
  StepResult,
  TruthInfo,
  VerificationCheck,
  VerificationRequest,
  VerificationResult,
  CvPoint,
} from "./types";
import {
  fastCharge,
  didtAt,
  relaxState,
  settleState,
  walkProfile,
  type ModelState,
  type VoltKnot,
  type WalkRange,
} from "./model";
import { apertureFromConfig, apertureTimes, gauss, mulberry32, pickRange, smuNoiseConfig } from "./smu";
import { analyseStep, type StepAnalysisInput, type StepAnalysisOutput } from "./analysis";
import { formatCoulombs, formatSeconds } from "./format";

// ============================== Утилиты ==============================

function sum(arr: number[]): number {
  let s = 0;
  for (const v of arr) s += v;
  return s;
}

function uuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "run-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/** Числовые поля паспорта программы (§5), проверяемые на конечность. */
const NUMERIC_CONFIG_FIELDS: (keyof MeasurementConfig)[] = [
  "vStart_V", "vEnd_V", "vStep_V", "iCompliance_A", "iRange_A", "nplc", "aperture_s",
  "lineFreq_Hz", "trigInterval_s", "trigCount", "tranDelay_s", "acqDelay_s", "tranTime_s",
  "preBias_V", "preBiasDuration_s", "settleDIdt_A_s", "tailPoints", "tailLimit_A",
  "slopeLimit_A_s", "kFactor", "convergencePct", "gapCurrentBound_A", "uCurrentCommon_A",
  "uLeakCommon_A", "uClockRel", "uDv_V", "uIOffset_A", "uIGainRel", "iOffset_A",
];

/**
 * Валидация паспорта программы (§5). Бросает Error с русским сообщением —
 * маршрут /api/run переводит это в 400.
 */
export function validateConfig(cfg: MeasurementConfig): void {
  const bad = (msg: string): never => {
    throw new Error(msg);
  };
  if (!cfg || typeof cfg !== "object") bad("Некорректный паспорт программы");
  for (const f of NUMERIC_CONFIG_FIELDS) {
    const v = cfg[f] as unknown;
    if (typeof v !== "number" || !isFinite(v)) {
      bad(`Нечисловое значение поля ${String(f)}`);
    }
  }
  if (typeof cfg.cycles !== "number" || !isFinite(cfg.cycles) || cfg.cycles < 1) {
    bad("cycles должен быть целым ≥ 1");
  }
  if (!Array.isArray(cfg.directions) || cfg.directions.length === 0) {
    bad("directions: укажите хотя бы одно направление (forward/reverse)");
  }
  const dirSet = new Set(cfg.directions);
  if (dirSet.size !== cfg.directions.length) bad("directions: дубликаты направлений");
  for (const d of cfg.directions) {
    if (d !== "forward" && d !== "reverse") bad(`directions: недопустимое направление «${String(d)}»`);
  }
  if (cfg.vStep_V <= 0) bad("Шаг напряжения vStep_V должен быть > 0");
  if (cfg.vEnd_V === cfg.vStart_V) bad("vEnd_V не должен совпадать с vStart_V");
  if (cfg.lineFreq_Hz <= 0) bad("Частота сети lineFreq_Hz должна быть > 0");
  if (cfg.iCompliance_A <= 0) bad("Комплаенс iCompliance_A должен быть > 0");
  if (cfg.iRangeMode === "fixed" && cfg.iRange_A <= 0) bad("Фиксированный диапазон iRange_A должен быть > 0");
  if (cfg.trigInterval_s <= 0) bad("Период триггера trigInterval_s должен быть > 0");
  if (cfg.tranDelay_s < 0) bad("Задержка источника tranDelay_s должна быть ≥ 0");
  if (cfg.acqDelay_s < 0) bad("Задержка измерения acqDelay_s должна быть ≥ 0");
  if (cfg.tranTime_s <= 0) bad("Длительность перехода источника tranTime_s должна быть > 0");
  if (cfg.preBiasDuration_s < 0) bad("Длительность подготовки preBiasDuration_s должна быть ≥ 0");
  if (cfg.tailPoints < 3) bad("tailPoints должен быть ≥ 3");
  if (cfg.tailLimit_A <= 0) bad("Допуск хвоста tailLimit_A должен быть > 0");
  if (cfg.slopeLimit_A_s <= 0) bad("Допуск наклона slopeLimit_A_s должен быть > 0");
  if (cfg.kFactor <= 0) bad("Коэффициент k должен быть > 0");
  if (
    cfg.uCurrentCommon_A < 0 || cfg.uLeakCommon_A < 0 || cfg.uClockRel < 0 ||
    cfg.uDv_V < 0 || cfg.uIOffset_A < 0 || cfg.uIGainRel < 0
  ) {
    bad("Неопределённости не могут быть отрицательными");
  }
  if (cfg.gapMode === "model" && cfg.fitModel === "none") {
    bad("Коррекция пропусков по модели требует fitModel = exp1 или exp2");
  }
  if (cfg.gapMode === "bound" && !(cfg.gapCurrentBound_A > 0)) {
    bad("Режим пропусков bound требует gapCurrentBound_A > 0");
  }

  const aperture = apertureFromConfig(cfg);
  if (cfg.protocol === "aperture_charge") {
    if (cfg.trigCount < 8) {
      bad("Протокол B (aperture_charge) требует trigCount ≥ 8 окон на шаг (анализ хвоста)");
    }
    if (aperture > cfg.trigInterval_s) {
      bad(
        `Длительность апертуры (${aperture} с) превышает период триггера (${cfg.trigInterval_s} с): ` +
          "уменьшите апертуру или увеличьте интервал",
      );
    }
    if (cfg.uIOffset_A <= 0 && cfg.uIGainRel <= 0) {
      bad("Для протокола B требуется положительная неопределённость тока (uIOffset_A или uIGainRel)");
    }
  } else {
    if (cfg.acqDelay_s + aperture > cfg.trigInterval_s) {
      bad(
        "Протокол A: апертура должна укладываться в период ступени (acqDelay + апертура ≤ trigInterval)",
      );
    }
    if (cfg.tranDelay_s + cfg.tranTime_s > cfg.trigInterval_s) {
      bad("Протокол A: переход источника должен завершаться внутри периода ступени");
    }
  }
}

/** Узлы профиля напряжения шага: 3 опорные точки + все границы окон. */
function buildStepKnots(
  vPrev: number,
  vNext: number,
  tranDelay: number,
  tranTime: number,
  endT: number,
  winA: number[],
  winB: number[],
): VoltKnot[] {
  const vAt = (t: number): number => {
    if (t <= tranDelay) return vPrev;
    if (t >= tranDelay + tranTime) return vNext;
    return vPrev + ((vNext - vPrev) * (t - tranDelay)) / tranTime;
  };
  const times = new Set<number>([0, tranDelay, tranDelay + tranTime, endT]);
  for (const aj of winA) times.add(aj);
  for (const bj of winB) times.add(bj);
  return [...times]
    .sort((p, q) => p - q)
    .map((t) => ({ t, v: vAt(t) }));
}

function rangeTotal(rg: WalkRange): number {
  return rg.qFast + rg.qLeak + sum(rg.qBranch);
}

/** Диапазоны пропусков: [0, a_1], между окнами, [b_N, endT]. */
function gapRangesFor(
  winA: number[],
  winB: number[],
  endT: number,
  mk: (l: number, r: number) => WalkRange,
): WalkRange[] {
  const out: WalkRange[] = [];
  if (winA[0] > 0) out.push(mk(0, winA[0]));
  for (let j = 0; j + 1 < winA.length; j++) {
    if (winA[j + 1] > winB[j]) out.push(mk(winB[j], winA[j + 1]));
  }
  if (endT > winB[winB.length - 1]) out.push(mk(winB[winB.length - 1], endT));
  return out;
}

// ============================== Выполнение измерения ==============================

export interface EngineResult {
  runs: RunResult[];
}

/**
 * Выполнение последовательности (§7): циклы × направления × шаги.
 * Состояние DUT переносится непрерывно; остановка §7.3 прекращает выполнение
 * всех последующих шагов.
 */
export function executeMeasurement(
  cfg: MeasurementConfig,
  dut: DutModel,
  seed = 31,
): RunResult[] {
  validateConfig(cfg);
  const rng = mulberry32(seed);
  const noise = smuNoiseConfig(cfg);
  const aperture = apertureFromConfig(cfg);
  const { a: winA, b: winB } = apertureTimes(cfg);
  const N = winA.length;
  const isProtoB = cfg.protocol === "aperture_charge";
  const effTailPoints = Math.min(cfg.tailPoints, N);
  const nSteps = Math.max(1, Math.round(Math.abs(cfg.vEnd_V - cfg.vStart_V) / cfg.vStep_V));

  // Точки развёртки: v_i = vStart + i·vStep, последняя точно vEnd.
  const fwdPoints: number[] = [];
  for (let i = 0; i <= nSteps; i++) {
    fwdPoints.push(i === nSteps ? cfg.vEnd_V : cfg.vStart_V + i * cfg.vStep_V);
  }

  // Подготовка §7.1: релаксация на preBias; если начальное напряжение первой
  // развёртки отличается от preBias — не наблюдаемый переход к нему и повторная
  // выдержка той же длительности (состояние ветвей непрерывно переносится).
  let maxTau = 0;
  for (const br of dut.branches) maxTau = Math.max(maxTau, br.tau_s);
  let state0 = relaxState(settleState(cfg.preBias_V, dut), cfg.preBias_V, cfg.preBiasDuration_s, dut);
  const firstDir = cfg.directions[0];
  const firstPoint = firstDir === "forward" ? fwdPoints[0] : fwdPoints[nSteps];
  if (Math.abs(firstPoint - cfg.preBias_V) > 1e-12) {
    state0 = relaxState(state0, firstPoint, cfg.preBiasDuration_s, dut);
  }
  const prebiasConfirmed =
    cfg.preBiasDuration_s >= 5 * maxTau &&
    (cfg.settleDIdt_A_s <= 0 || didtAt(state0, firstPoint, dut) <= cfg.settleDIdt_A_s);

  const runs: RunResult[] = [];
  const tailBHistory: number[] = [];
  let executedSteps = 0;
  let stopReason: string | null = null;
  let state: ModelState = state0;

  const mkRange = (l: number, r: number): WalkRange => ({
    l,
    r,
    qFast: 0,
    qBranch: dut.branches.map(() => 0),
    qLeak: 0,
  });

  const analysisOptions: AnalyzeOptions = {
    model: cfg.fitModel,
    gapMode: cfg.gapMode,
    gapCurrentBound_A: cfg.gapCurrentBound_A,
    tailPoints: effTailPoints,
    tailLimit_A: cfg.tailLimit_A,
    slopeLimit_A_s: cfg.slopeLimit_A_s,
    kFactor: cfg.kFactor,
    uCurrentCommon_A: cfg.uCurrentCommon_A,
    uLeakCommon_A: cfg.uLeakCommon_A,
    uClockRel: cfg.uClockRel,
    uDv_V: cfg.uDv_V,
  };

  const makeRun = (direction: Direction, cycle: number): RunResult => ({
    id: uuid(),
    label: `${direction === "forward" ? "Прямой" : "Обратный"} ход${cfg.cycles > 1 ? `, цикл ${cycle}` : ""}`,
    direction,
    cycle,
    protocol: cfg.protocol,
    config: cfg,
    dut,
    steps: [],
    stopped: false,
    stopReason: null,
    cvCurve: [],
    wPortTotal_J: 0,
    dUTotal_J: 0,
    dissipatedTotal_J: 0,
    loopArea_J: 0,
    timing: {
      tranDelay_s: cfg.tranDelay_s,
      acqDelay_s: cfg.acqDelay_s,
      tranTime_s: cfg.tranTime_s,
      interval_s: cfg.trigInterval_s,
      aperture_s: aperture,
      firstA_s: winA[0],
      firstB_s: winB[0],
      lastB_s: winB[N - 1],
      initialGap_s: winA[0] - (cfg.tranDelay_s + cfg.tranTime_s),
      deadTime_s: cfg.trigInterval_s - aperture,
    },
    createdAt: new Date().toISOString(),
  });

  /** Один шаг перехода vPrev → vNext. Возвращает шаг, состояние после и причину остановки. */
  const runStep = (
    direction: Direction,
    k: number,
    vPrev: number,
    vNext: number,
    preSettled: boolean,
  ): { step: StepResult; stateAfter: ModelState; stop: string | null } => {
    const dv = vNext - vPrev;
    const vMid = (vPrev + vNext) / 2;
    const endT = isProtoB
      ? Math.max(winB[N - 1], cfg.tranDelay_s + cfg.tranTime_s)
      : cfg.trigInterval_s;

    const knots = buildStepKnots(vPrev, vNext, cfg.tranDelay_s, cfg.tranTime_s, endT, winA, winB);
    const apRanges = winA.map((aj, j) => mkRange(aj, winB[j]));
    const gapRanges = gapRangesFor(winA, winB, endT, mkRange);
    const initialGapRange = winA[0] > 0 ? gapRanges[0] : null;
    const walk = walkProfile(knots, state, [...apRanges, ...gapRanges], dut);
    const stateAfter = walk.stateEnd;

    // ---- Измерение апертур (шум + offset прибора, диапазон, комплаенс) ----
    const apertures: ApertureRecord[] = [];
    const iMeasArr: number[] = [];
    const uIArr: number[] = [];
    const stepFlagSet = new Set<string>();
    for (let j = 0; j < N; j++) {
      const dt = winB[j] - winA[j];
      const iAvgTrue = rangeTotal(apRanges[j]) / dt;
      const iMeas = iAvgTrue + noise.offset + gauss(rng) * noise.uI(Math.abs(iAvgTrue));
      const uIRec = noise.uI(Math.abs(iMeas));
      const { overload } = pickRange(Math.abs(iMeas), cfg);
      let status = "OK";
      if (overload) {
        status = "OVLD";
        stepFlagSet.add("RANGE_OVERLOAD");
      }
      if (Math.abs(iMeas) > cfg.iCompliance_A) {
        status = "COMP";
        stepFlagSet.add("COMPLIANCE_EXCEEDED");
      }
      iMeasArr.push(iMeas);
      uIArr.push(uIRec);
      apertures.push({
        j: j + 1,
        a_s: winA[j],
        b_s: winB[j],
        i_avg_A: iMeas,
        i_background_A: 0,
        u_i_A: uIRec,
        status,
      });
    }

    // ---- Истина модели (§13: «истина» перехода) ----
    const qFastTotal = fastCharge(vNext, dut) - fastCharge(vPrev, dut);
    const qBranchTotal = dut.branches.map((br, m) => {
      const x0 = m < state.x.length ? state.x[m] : 0;
      const x1 = m < stateAfter.x.length ? stateAfter.x[m] : 0;
      return br.c_F * (x1 - x0);
    });
    let qEqBranches = 0;
    for (const br of dut.branches) qEqBranches += br.c_F * (vNext - vPrev);
    const qTotal = qFastTotal + qEqBranches;
    let qRegistered = 0;
    for (const rg of apRanges) qRegistered += rangeTotal(rg);
    let qInGaps = 0;
    for (const rg of gapRanges) qInGaps += rg.qFast + sum(rg.qBranch);
    const truth: TruthInfo = {
      qFast_C: qFastTotal,
      qBranch_C: qBranchTotal,
      qLeak_C: walk.qLeak,
      qRegistered_C: qRegistered,
      qInGaps_C: qInGaps,
      qTotal_C: qTotal,
      cSecTrue_F: qTotal / dv,
    };

    // ---- Анализ ----
    const uDt = winA.map((_, j) => (cfg.uClockRel > 0 ? cfg.uClockRel * (winB[j] - winA[j]) : 0));
    const uLeakInd = winA.map(() => 0);
    let analysis: StepAnalysisOutput;

    if (isProtoB) {
      const baseInput: StepAnalysisInput = {
        a: winA,
        b: winB,
        i: iMeasArr,
        uI: uIArr,
        leak: winA.map(() => 0),
        uLeakInd,
        uDt,
        preSettled,
        dv,
        vBefore: vPrev,
        vAfter: vNext,
        options: analysisOptions,
      };
      let leak = winA.map(() => 0);
      if (cfg.backgroundMode === "mean_tail" || cfg.backgroundMode === "linear_tail") {
        // Проход 1: подгонка хвоста без фона.
        const pass1 = analyseStep({ ...baseInput, options: { ...analysisOptions, model: "none" } });
        const p1bad = pass1.flags.some((f) => f.startsWith("DATA_INVALID"));
        if (!p1bad && isFinite(pass1.tailB)) {
          if (cfg.backgroundMode === "mean_tail") {
            leak = winA.map(() => pass1.tailB);
          } else {
            let tMean = 0;
            for (let j = N - effTailPoints; j < N; j++) tMean += (winA[j] + winB[j]) / 2;
            tMean /= effTailPoints;
            leak = winA.map(
              (_, j) => pass1.tailB + pass1.tailS * ((winA[j] + winB[j]) / 2 - tMean),
            );
          }
        }
      } else if (cfg.backgroundMode === "blank") {
        stepFlagSet.add("BLANK_UNAVAILABLE");
      }
      for (let j = 0; j < N; j++) apertures[j].i_background_A = leak[j];
      analysis = analyseStep({ ...baseInput, leak, options: analysisOptions });
    } else {
      // Протокол A: одиночное окно, Kuhn C = |Ī/r|; анализ хвоста неприменим.
      const L1 = 0;
      if (cfg.backgroundMode !== "none") stepFlagSet.add("BACKGROUND_UNAVAILABLE");
      apertures[0].i_background_A = L1;
      const r = dv / cfg.trigInterval_s;
      const dt = winB[0] - winA[0];
      const qObs = (iMeasArr[0] - L1) * dt;
      const uQ = uIArr[0] * dt;
      analysis = {
        qObs,
        uQ,
        qGapModel: 0,
        qGapBound: 0,
        qCorrected: qObs,
        cSec: Math.abs((iMeasArr[0] - L1) / r),
        uC: uIArr[0] / Math.abs(r),
        tailB: 0,
        tailS: 0,
        tailUB: 0,
        tailUS: 0,
        tailOk: true,
        gaps: [],
        gapDuration: 0,
        fit: null,
        flags: [],
        usableForPair: preSettled,
      };
    }

    // ---- Флаги шага ----
    const flags = new Set<string>(analysis.flags);
    for (const f of stepFlagSet) flags.add(f);
    if (!preSettled) flags.add("FIRST_STEP_DIAGNOSTIC");
    const initialGap = winA[0] - (cfg.tranDelay_s + cfg.tranTime_s);
    if (initialGap > 0) {
      flags.add("MISSED_START");
    } else if (
      winA[0] > 0 &&
      initialGapRange &&
      Math.abs(initialGapRange.qFast) > 0.5 * Math.abs(qFastTotal)
    ) {
      flags.add("MISSED_START");
    }
    if (winA[0] > 0) flags.add("INITIAL_GAP_PRESENT");

    // ---- Сборка StepResult ----
    const step: StepResult = {
      step: k,
      direction,
      vBefore_V: vPrev,
      vAfter_V: vNext,
      vMid_V: vMid,
      dv_V: dv,
      preSettled,
      apertures,
      gaps_s: analysis.gaps,
      gapDuration_s: analysis.gapDuration,
      qObs_C: analysis.qObs,
      uQ_C: analysis.uQ,
      qGapModel_C: analysis.qGapModel,
      qGapBound_C: analysis.qGapBound,
      qCorrected_C: analysis.qCorrected,
      cSec_F: analysis.cSec,
      uC_F: analysis.uC,
      tailB_A: analysis.tailB,
      tailS_A_s: analysis.tailS,
      tailUB_A: analysis.tailUB,
      tailUS_A_s: analysis.tailUS,
      tailOk: analysis.tailOk,
      fit: analysis.fit,
      wPort_J: walk.wPort,
      dU_J: walk.dU,
      dissipated_J: walk.dissipated,
      energyResidual_J: walk.residual,
      flags: Array.from(flags),
      usableForPair: analysis.usableForPair,
      truth,
    };
    if (!isProtoB) {
      const r = dv / cfg.trigInterval_s;
      step.kuhn = {
        iMeas_A: iMeasArr[0],
        rampRate_Vps: r,
        cKuhn_F: Math.abs(iMeasArr[0] / r),
        cKuhnLeakCorrected_F: Math.abs((iMeasArr[0] - apertures[0].i_background_A) / r),
        apertureStart_s: winA[0],
      };
    }

    // ---- Условия остановки §7.3 ----
    tailBHistory.push(Math.abs(analysis.tailB));
    let stop: string | null = null;
    if (stepFlagSet.has("RANGE_OVERLOAD")) {
      stop = "OVERLOAD";
    } else if (stepFlagSet.has("COMPLIANCE_EXCEEDED")) {
      stop = "COMPLIANCE";
    } else {
      if (
        Math.abs(analysis.tailB) > 10 * cfg.tailLimit_A &&
        tailBHistory.length >= 3
      ) {
        const L = tailBHistory.length;
        if (tailBHistory[L - 3] < tailBHistory[L - 2] && tailBHistory[L - 2] < tailBHistory[L - 1]) {
          stop = "BREAKDOWN_OR_LEAKAGE_GROWTH";
        }
      }
      if (!stop && isProtoB) {
        // Неустойчивость: знакочередующиеся остатки хвоста ≥ 5 окон подряд.
        let prevSign = 0;
        let alt = 0;
        let maxAlt = 0;
        let tMean = 0;
        for (let j = N - effTailPoints; j < N; j++) tMean += (winA[j] + winB[j]) / 2;
        tMean /= effTailPoints;
        for (let j = N - effTailPoints; j < N; j++) {
          const yj = iMeasArr[j] - apertures[j].i_background_A;
          const uj = uIArr[j];
          const res = yj - (analysis.tailB + analysis.tailS * ((winA[j] + winB[j]) / 2 - tMean));
          if (Math.abs(res) > 2 * uj) {
            const sg = Math.sign(res);
            if (sg === prevSign) alt = 1;
            else alt = prevSign === 0 ? 1 : alt + 1;
            prevSign = sg;
            if (alt > maxAlt) maxAlt = alt;
          } else {
            prevSign = 0;
            alt = 0;
          }
        }
        if (maxAlt >= 5) stop = "INSTABILITY";
      }
      if (!stop) {
        const qMissed = initialGapRange
          ? initialGapRange.qFast + sum(initialGapRange.qBranch)
          : 0;
        if (
          initialGap > 0 &&
          Math.abs(qMissed) > (cfg.convergencePct / 100) * Math.abs(qTotal)
        ) {
          stop = "TIMING_LOSS";
        }
      }
      if (!stop && cfg.stopOnTailFail && !analysis.tailOk) {
        stop = "TAIL_NOT_SETTLED";
      }
    }

    return { step, stateAfter, stop };
  };

  // ---- Циклы × направления × шаги ----
  for (let cycle = 1; cycle <= cfg.cycles && !stopReason; cycle++) {
    for (const direction of cfg.directions) {
      if (stopReason) break;
      const points = direction === "forward" ? fwdPoints : [...fwdPoints].reverse();
      let run: RunResult | null = null;
      for (let k = 0; k < nSteps; k++) {
        if (stopReason) break;
        const vPrev = points[k];
        const vNext = points[k + 1];
        const isFirstOverall = executedSteps === 0;
        // §7.1/§7.3: первый шаг ветви без отдельной подготовки — false (история разворота).
        const preSettled = isFirstOverall ? prebiasConfirmed : k > 0;
        const { step, stateAfter, stop } = runStep(direction, k, vPrev, vNext, preSettled);
        state = stateAfter;
        if (!run) {
          run = makeRun(direction, cycle);
          runs.push(run);
        }
        run.steps.push(step);
        run.cvCurve.push({
          vMid_V: step.vMid_V,
          cSec_F: step.cSec_F,
          uC_F: step.uC_F,
          cSecTrue_F: step.truth ? step.truth.cSecTrue_F : null,
          direction,
          usable: step.usableForPair,
        } satisfies CvPoint);
        run.wPortTotal_J += step.wPort_J;
        run.dUTotal_J += step.dU_J;
        run.dissipatedTotal_J += step.dissipated_J;
        executedSteps++;
        if (stop) {
          stopReason = stop;
          run.stopped = true;
          run.stopReason = stop;
        }
      }
    }
  }

  // ---- Площадь петли ∮V dQ по циклу (прямой + обратный ход, §10) ----
  const byCycle = new Map<number, RunResult[]>();
  for (const run of runs) {
    if (!byCycle.has(run.cycle)) byCycle.set(run.cycle, []);
    byCycle.get(run.cycle)!.push(run);
  }
  for (const rs of byCycle.values()) {
    const fwd = rs.find((r) => r.direction === "forward");
    const rev = rs.find((r) => r.direction === "reverse");
    if (!fwd || !rev) continue;
    const path = [...fwd.steps, ...rev.steps];
    if (path.length < 2) continue;
    const qs: number[] = [];
    let qAcc = 0;
    for (const st of path) {
      qs.push(qAcc);
      qAcc += st.qObs_C;
    }
    let area = 0;
    for (let k = 0; k < path.length; k++) {
      const qNext = k + 1 < path.length ? qs[k + 1] : 0; // замыкание петли: Q → Q_0 = 0
      area += path[k].vMid_V * (qNext - qs[k]);
    }
    fwd.loopArea_J = Math.abs(area);
    rev.loopArea_J = Math.abs(area);
  }

  return runs;
}

// ============================== Верификация (§6) ==============================

/**
 * Проверка восстановления известного заряда C·ΔV на эталоне
 * (конденсатор или RC), одиночный переход 0 → dv_V.
 */
export function executeVerification(req: VerificationRequest): VerificationResult {
  if (!req || typeof req !== "object") throw new Error("Верификация: некорректный запрос");
  const t = req.timing;
  const numeric = [
    req.cRef_F, req.rRef_ohm, req.dv_V, req.tolerancePct, req.noise_A,
    t.interval_s, t.aperture_s, t.tranDelay_s, t.acqDelay_s, t.tranTime_s, t.count,
  ];
  for (const v of numeric) {
    if (typeof v !== "number" || !isFinite(v)) {
      throw new Error("Верификация: все параметры должны быть конечными числами");
    }
  }
  if (req.refType !== "c" && req.refType !== "rc") {
    throw new Error("Верификация: refType должен быть «c» или «rc»");
  }
  if (req.cRef_F <= 0) throw new Error("Верификация: cRef_F должен быть > 0");
  if (req.dv_V === 0) throw new Error("Верификация: dv_V не должен быть нулевым");
  if (req.tolerancePct <= 0) throw new Error("Верификация: допуск tolerancePct должен быть > 0");
  if (req.noise_A < 0) throw new Error("Верификация: шум не может быть отрицательным");
  if (req.refType === "rc" && !(req.rRef_ohm > 0)) {
    throw new Error("Верификация: для refType = rc требуется rRef_ohm > 0");
  }
  if (t.count < 1) throw new Error("Верификация: количество апертур должно быть ≥ 1");
  if (t.aperture_s <= 0) throw new Error("Верификация: апертура должна быть > 0");
  if (t.interval_s <= 0) throw new Error("Верификация: интервал триггера должен быть > 0");
  if (t.aperture_s > t.interval_s) {
    throw new Error("Верификация: апертура превышает интервал триггера (окна перекрываются)");
  }
  if (t.tranDelay_s < 0 || t.acqDelay_s < 0) {
    throw new Error("Верификация: задержки не могут быть отрицательными");
  }
  if (t.tranTime_s <= 0) throw new Error("Верификация: длительность фронта должна быть > 0");

  const dut: DutModel =
    req.refType === "c"
      ? {
          kind: "ref_c",
          label: `Эталонный конденсатор ${req.cRef_F} Ф`,
          c0_F: req.cRef_F,
          g_S: 0,
          branches: [],
          cOx_F: 0,
          cMin_F: 0,
          vfb_V: 0,
          width_V: 0,
        }
      : {
          kind: "ref_rc",
          label: `Эталон RC: ${req.cRef_F} Ф, R = ${req.rRef_ohm} Ом`,
          c0_F: 0,
          g_S: 0,
          branches: [{ c_F: req.cRef_F, tau_s: req.rRef_ohm * req.cRef_F }],
          cOx_F: 0,
          cMin_F: 0,
          vfb_V: 0,
          width_V: 0,
        };

  const N = Math.max(1, Math.round(t.count));
  const winA: number[] = [];
  const winB: number[] = [];
  for (let j = 1; j <= N; j++) {
    const aj = (j - 1) * t.interval_s + t.acqDelay_s;
    winA.push(aj);
    winB.push(aj + t.aperture_s);
  }
  const vPrev = 0;
  const vNext = req.dv_V;
  const endT = Math.max(winB[N - 1], t.tranDelay_s + t.tranTime_s);

  const knots = buildStepKnots(vPrev, vNext, t.tranDelay_s, t.tranTime_s, endT, winA, winB);
  const mkRange = (l: number, r: number): WalkRange => ({
    l,
    r,
    qFast: 0,
    qBranch: dut.branches.map(() => 0),
    qLeak: 0,
  });
  const apRanges = winA.map((aj, j) => mkRange(aj, winB[j]));
  const gapRanges = gapRangesFor(winA, winB, endT, mkRange);
  const state0 = settleState(vPrev, dut);
  walkProfile(knots, state0, [...apRanges, ...gapRanges], dut);

  // Шум: постоянная неопределённость noise_A, смещения нет, seed 31.
  const rng = mulberry32(31);
  const apertures: ApertureRecord[] = [];
  const iMeasArr: number[] = [];
  for (let j = 0; j < N; j++) {
    const dt = winB[j] - winA[j];
    const iAvgTrue = rangeTotal(apRanges[j]) / dt;
    const iMeas = iAvgTrue + gauss(rng) * req.noise_A;
    iMeasArr.push(iMeas);
    apertures.push({
      j: j + 1,
      a_s: winA[j],
      b_s: winB[j],
      i_avg_A: iMeas,
      i_background_A: 0,
      u_i_A: req.noise_A,
      status: "OK",
    });
  }

  let qRecovered = 0; // ΔQ_obs = Σ Ī_j·dt_j (фон 0)
  for (let j = 0; j < N; j++) qRecovered += iMeasArr[j] * (winB[j] - winA[j]);
  const qExpected = req.cRef_F * req.dv_V;
  const errorRelPct = ((qRecovered - qExpected) / qExpected) * 100;
  const pass = Math.abs(errorRelPct) <= req.tolerancePct;

  let qRegisteredTrue = 0;
  for (const rg of apRanges) qRegisteredTrue += rangeTotal(rg);
  let qInGaps = 0;
  for (const rg of gapRanges) qInGaps += rg.qFast + sum(rg.qBranch);

  const badStatus = apertures.some((ap) => ap.status !== "OK");

  const checks: VerificationCheck[] = [
    {
      label: "Начало изменения напряжения",
      value: `t = ${formatSeconds(t.tranDelay_s)}, фронт ${formatSeconds(t.tranTime_s)}`,
      ok: null,
    },
    {
      label: "Задержка до первой апертуры",
      value: formatSeconds(winA[0]),
      ok: winA[0] <= t.tranDelay_s + t.tranTime_s,
    },
    {
      label: "Длительность апертуры / вес",
      value: formatSeconds(t.aperture_s),
      ok: t.aperture_s > 0,
    },
    {
      label: "Мёртвое время между апертурами",
      value: formatSeconds(t.interval_s - t.aperture_s),
      ok: null,
      note: "период таймера ≠ апертура (§6)",
    },
    {
      label: "Перегрузка / compliance",
      value: badStatus ? "обнаружены" : "не обнаружены",
      ok: !badStatus,
    },
    {
      label: "Восстановление известного заряда C·ΔV",
      value: `${errorRelPct.toExponential(3)} %`,
      ok: pass,
    },
    {
      label: "Заряд в пропусках (истина)",
      value: formatCoulombs(qInGaps),
      ok: null,
      note: "если ≠ 0 — часть заряда не зарегистрирована",
    },
  ];

  return {
    apertures,
    qExpected_C: qExpected,
    qRecovered_C: qRecovered,
    qRegisteredTrue_C: qRegisteredTrue,
    qInGaps_C: qInGaps,
    errorRelPct,
    tolerancePct: req.tolerancePct,
    pass,
    checks,
    timing: {
      tranDelay_s: t.tranDelay_s,
      acqDelay_s: t.acqDelay_s,
      tranTime_s: t.tranTime_s,
      interval_s: t.interval_s,
      aperture_s: t.aperture_s,
      firstA_s: winA[0],
      firstB_s: winB[0],
      lastB_s: winB[N - 1],
      initialGap_s: winA[0] - (t.tranDelay_s + t.tranTime_s),
      deadTime_s: t.interval_s - t.aperture_s,
    },
  };
}
