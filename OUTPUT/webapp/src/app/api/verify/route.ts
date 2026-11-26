import { NextRequest, NextResponse } from "next/server";
import { executeVerification } from "@/lib/qscv/engine";
import type { VerificationRequest } from "@/lib/qscv/types";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as VerificationRequest;
    const result = executeVerification(body);
    return NextResponse.json(result);
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}
