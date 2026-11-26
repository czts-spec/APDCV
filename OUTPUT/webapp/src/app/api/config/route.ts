import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const rows = await db.measurementConfig.findMany({
      orderBy: { createdAt: "desc" },
      take: 50,
      select: { id: true, name: true, createdAt: true },
    });
    return NextResponse.json(rows);
  } catch (e) {
    return NextResponse.json([], { status: 200 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { config: unknown; dutLabel?: string };
    if (!body || typeof body !== "object" || !body.config) {
      return NextResponse.json({ error: "Требуется config" }, { status: 400 });
    }
    const cfg = body.config as { name?: string };
    const row = await db.measurementConfig.create({
      data: {
        name: String(cfg.name ?? "Без названия"),
        data: JSON.stringify({ config: body.config, dutLabel: body.dutLabel ?? null }),
      },
    });
    return NextResponse.json({ id: row.id });
  } catch (e) {
    return NextResponse.json(
      { error: `Не удалось сохранить конфигурацию: ${(e as Error).message}` },
      { status: 400 },
    );
  }
}
