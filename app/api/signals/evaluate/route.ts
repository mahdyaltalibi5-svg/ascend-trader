import { NextRequest } from "next/server";
import { BOT_API_URL } from "@/lib/constants";

export async function POST(req: NextRequest) {
  try {
    const url = new URL(req.url);
    const limit = url.searchParams.get("limit") ?? "40";
    const res = await fetch(`${BOT_API_URL}/signals/evaluate?limit=${limit}`, {
      method: "POST",
      signal: AbortSignal.timeout(90_000),
    });

    if (!res.ok) {
      const text = await res.text();
      let error = text || "Outcome evaluation failed";
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
