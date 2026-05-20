import { NextRequest } from "next/server";
import { BOT_API_URL } from "@/lib/constants";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const res = await fetch(`${BOT_API_URL}/backtest`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(120_000),
    });

    if (!res.ok) {
      const text = await res.text();
      let error = text || "Backtest failed";
      try {
        const parsed = JSON.parse(text) as { detail?: string; error?: string };
        error = parsed.error ?? parsed.detail ?? error;
      } catch {}
      return Response.json({ error }, { status: 502 });
    }

    return Response.json(await res.json());
  } catch {
    return Response.json({ error: "Bot API not reachable" }, { status: 502 });
  }
}
