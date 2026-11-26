import { NextResponse } from "next/server";
import { db } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const rows = await db.run.findMany({
      orderBy: { createdAt: "desc" },
      take: 50,
      select: {
        id: true,
        label: true,
        protocol: true,
        direction: true,
        cycle: true,
        stepsCount: true,
        stopped: true,
        stopReason: true,
        createdAt: true,
      },
    });
    return NextResponse.json(rows);
  } catch {
    return NextResponse.json([]);
  }
}
