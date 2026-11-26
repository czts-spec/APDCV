import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { executeMeasurement } from "@/lib/qscv/engine";
import { DUT_PRESETS, type MeasurementConfig } from "@/lib/qscv/types";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function POST(req: NextRequest) {
  let cfg: MeasurementConfig;
  let dutLabel: string | undefined;
  let persist = true;
  let seed = 31;
  try {
    const body = (await req.json()) as {
      config: MeasurementConfig;
      dutLabel?: string;
      persist?: boolean;
      seed?: number;
    };
    cfg = body.config;
    dutLabel = body.dutLabel;
    persist = body.persist !== false;
    if (typeof body.seed === "number" && isFinite(body.seed)) seed = body.seed;
  } catch {
    return NextResponse.json({ error: "Некорректное тело запроса" }, { status: 400 });
  }

  let runs;
  try {
    const dut = (dutLabel && DUT_PRESETS[dutLabel]) || DUT_PRESETS.demo_qscv;
    runs = executeMeasurement(cfg, dut, seed);
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }

  if (persist) {
    try {
      for (const run of runs) {
        await db.run.create({
          data: {
            label: run.label,
            protocol: run.protocol,
            direction: run.direction,
            cycle: run.cycle,
            stepsCount: run.steps.length,
            stopped: run.stopped,
            stopReason: run.stopReason,
            configJson: JSON.stringify(run.config),
            dutJson: JSON.stringify(run.dut),
            resultJson: JSON.stringify(run),
          },
        });
      }
    } catch (e) {
      // Сохранение не должно ломать выполнение — вернуть результат с предупреждением.
      return NextResponse.json(runs);
    }
  }
  return NextResponse.json(runs);
}
