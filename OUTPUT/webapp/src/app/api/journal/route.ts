import { NextRequest, NextResponse } from "next/server";
import { journalCsv, journalMetadata, parseAcquisitionCsv } from "@/lib/qscv/csv";
import type { JournalFormat, RunResult } from "@/lib/qscv/types";

export const dynamic = "force-dynamic";

/** Экспорт журнала прогона: POST {run, format} → CSV (text/plain). */
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { run: RunResult; format: JournalFormat };
    if (!body?.run?.steps) {
      return NextResponse.json({ error: "Требуется run" }, { status: 400 });
    }
    const format: JournalFormat = body.format === "qscv_final" ? "qscv_final" : "methodology";
    const csv = journalCsv(body.run, format);
    return new NextResponse(csv, {
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": `attachment; filename="qscv_journal_${format}.csv"`,
      },
    });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}

/** Метаданные прогона: PATCH {run} → JSON настроек (§13). */
export async function PATCH(req: NextRequest) {
  try {
    const body = (await req.json()) as { run: RunResult };
    if (!body?.run?.config) {
      return NextResponse.json({ error: "Требуется run" }, { status: 400 });
    }
    return new NextResponse(journalMetadata(body.run), {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Disposition": 'attachment; filename="qscv_metadata.json"',
      },
    });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}

/** Импорт acquisition.csv: PUT {csvText} → {rows, errors}. */
export async function PUT(req: NextRequest) {
  try {
    const body = (await req.json()) as { csvText: string };
    if (typeof body?.csvText !== "string") {
      return NextResponse.json({ error: "Требуется csvText" }, { status: 400 });
    }
    const { rows, errors } = parseAcquisitionCsv(body.csvText);
    return NextResponse.json({ rows, errors });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}
