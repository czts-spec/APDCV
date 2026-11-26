import { NextRequest, NextResponse } from "next/server";
import { generateScpi } from "@/lib/qscv/scpi";
import { DEFAULT_CONFIG, type ScpiRequest } from "@/lib/qscv/types";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as Partial<ScpiRequest>;
    const request: ScpiRequest = {
      config: body.config ?? DEFAULT_CONFIG,
      protocol: body.protocol ?? "aperture_charge",
      direction: body.direction ?? "forward",
      includeReverse: Boolean(body.includeReverse),
    };
    return NextResponse.json(generateScpi(request));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}
