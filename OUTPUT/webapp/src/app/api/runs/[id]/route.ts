import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const row = await db.run.findUnique({ where: { id } });
    if (!row) {
      return NextResponse.json({ error: "Прогон не найден" }, { status: 404 });
    }
    return new NextResponse(row.resultJson, {
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 500 });
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    await db.run.delete({ where: { id } });
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ error: "Не удалось удалить прогон" }, { status: 400 });
  }
}
