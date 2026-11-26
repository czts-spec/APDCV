import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { DEFAULT_INSTRUMENT, type InstrumentPassport } from "@/lib/qscv/types";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const row = await db.instrumentPassport.findFirst({
      orderBy: { updatedAt: "desc" },
    });
    if (!row) return NextResponse.json(DEFAULT_INSTRUMENT);
    return NextResponse.json({
      id: row.id,
      model: row.model,
      serialNumber: row.serialNumber,
      firmware: row.firmware,
      calibrationDate: row.calibrationDate,
      temperatureC: row.temperatureC,
      warmupMinutes: row.warmupMinutes,
      selfCalDone: row.selfCalDone,
      selfTestDone: row.selfTestDone,
      wiring: row.wiring,
      notes: row.notes,
    } satisfies InstrumentPassport);
  } catch {
    return NextResponse.json(DEFAULT_INSTRUMENT);
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as InstrumentPassport;
    const data = {
      model: String(body.model ?? "B2910BL"),
      serialNumber: String(body.serialNumber ?? ""),
      firmware: String(body.firmware ?? ""),
      calibrationDate: String(body.calibrationDate ?? ""),
      temperatureC:
        typeof body.temperatureC === "number" && isFinite(body.temperatureC)
          ? body.temperatureC
          : null,
      warmupMinutes: Number.isFinite(body.warmupMinutes) ? Math.round(body.warmupMinutes) : 60,
      selfCalDone: Boolean(body.selfCalDone),
      selfTestDone: Boolean(body.selfTestDone),
      wiring: body.wiring === "2w" ? "2w" : "4w",
      notes: String(body.notes ?? ""),
    };
    const existing = await db.instrumentPassport.findFirst({
      orderBy: { updatedAt: "desc" },
    });
    const row = existing
      ? await db.instrumentPassport.update({ where: { id: existing.id }, data })
      : await db.instrumentPassport.create({ data });
    return NextResponse.json({ ...body, id: row.id });
  } catch (e) {
    return NextResponse.json(
      { error: `Не удалось сохранить паспорт прибора: ${(e as Error).message}` },
      { status: 400 },
    );
  }
}
