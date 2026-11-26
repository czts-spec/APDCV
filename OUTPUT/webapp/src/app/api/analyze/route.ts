import { NextRequest, NextResponse } from "next/server";
import { analyseStep, pairForwardReverseDetailed } from "@/lib/qscv/analysis";
import type {
  AcquisitionRow,
  AnalyzeOptions,
  AnalyzeStepRow,
  AnalyzeResult,
} from "@/lib/qscv/types";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const DEFAULT_OPTIONS: AnalyzeOptions = {
  model: "none",
  gapMode: "reject",
  gapCurrentBound_A: 0,
  tailPoints: 10,
  tailLimit_A: 1e-12,
  slopeLimit_A_s: 1e-10,
  kFactor: 1.96,
  uCurrentCommon_A: 0,
  uLeakCommon_A: 0,
  uClockRel: 0,
  uDv_V: 0,
};

export async function POST(req: NextRequest) {
  let rows: AcquisitionRow[];
  let options: AnalyzeOptions;
  try {
    const body = (await req.json()) as { rows: AcquisitionRow[]; options?: Partial<AnalyzeOptions> };
    rows = Array.isArray(body.rows) ? body.rows : [];
    options = { ...DEFAULT_OPTIONS, ...(body.options ?? {}) };
  } catch {
    return NextResponse.json({ error: "Некорректное тело запроса" }, { status: 400 });
  }
  if (rows.length === 0) {
    return NextResponse.json({ error: "Нет строк данных (rows пуст)" }, { status: 400 });
  }

  // Группировка по (run, direction, step) с сохранением порядка появления.
  const groups = new Map<string, AcquisitionRow[]>();
  const groupOrder: string[] = [];
  for (const r of rows) {
    const key = `${r.run}\u0000${r.direction}\u0000${r.step}`;
    if (!groups.has(key)) {
      groups.set(key, []);
      groupOrder.push(key);
    }
    groups.get(key)!.push(r);
  }

  const errors: string[] = [];
  const steps: AnalyzeStepRow[] = [];

  for (const key of groupOrder) {
    const g = groups.get(key)!;
    const [run, direction, stepStr] = key.split("\u0000");
    const label = `Прогон «${run}», ${direction}, шаг ${stepStr}`;
    g.sort((p, q) => p.a_s - q.a_s);

    const a = g.map((r) => r.a_s);
    const b = g.map((r) => r.b_s);
    const dv = g[0].v_after_V - g[0].v_before_V;

    // Проверки согласованности (порт qscv_final).
    const vBeforeSet = new Set(g.map((r) => r.v_before_V));
    const vAfterSet = new Set(g.map((r) => r.v_after_V));
    if (vBeforeSet.size !== 1 || vAfterSet.size !== 1) {
      errors.push(`${label}: неконсистентные напряжения v_before/v_after по окнам шага`);
      continue;
    }
    if (!isFinite(dv) || dv === 0) {
      errors.push(`${label}: нулевой или нечисловой шаг напряжения`);
      continue;
    }
    if (g.some((r) => !isFinite(r.i_A) || !isFinite(r.a_s) || !isFinite(r.b_s))) {
      errors.push(`${label}: нечисловые значения тока/времени`);
      continue;
    }

    const out = analyseStep({
      a,
      b,
      i: g.map((r) => r.i_A),
      uI: g.map((r) => r.u_i_A),
      leak: g.map((r) => r.leak_A),
      uLeakInd: g.map((r) => r.u_leak_ind_A),
      uDt: g.map((r) => r.u_dt_s),
      preSettled: g.every((r) => r.pre_settled),
      dv,
      vBefore: g[0].v_before_V,
      vAfter: g[0].v_after_V,
      options,
    });

    if (out.flags.some((f) => f.startsWith("DATA_INVALID"))) {
      errors.push(`${label}: ${out.flags.join("; ")}`);
      continue;
    }

    steps.push({
      run,
      direction,
      step: g[0].step,
      vMid_V: (g[0].v_before_V + g[0].v_after_V) / 2,
      qObserved_C: out.qObs,
      uQ_C: out.uQ,
      qGapModel_C: out.qGapModel,
      qGapBound_C: out.qGapBound,
      qCorrected_C: out.qCorrected,
      cSec_F: out.cSec,
      uC_F: out.uC,
      tailCurrent_A: out.tailB,
      tailSlope_A_s: out.tailS,
      tailSettled: out.tailOk,
      preSettled: g.every((r) => r.pre_settled),
      gapDuration_s: out.gapDuration,
      usableForPair: out.usableForPair,
      flags: out.flags,
      fit: out.fit,
      gaps_s: out.gaps,
    });
  }

  const { pairs, errors: pairErrors } = pairForwardReverseDetailed(steps);
  const result: AnalyzeResult = {
    steps,
    pairs,
    errors: [...errors, ...pairErrors],
  };
  return NextResponse.json(result);
}
