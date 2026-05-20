import { BOT_API_URL } from "@/lib/constants";

export async function POST() {
  try {
    const res = await fetch(`${BOT_API_URL}/scan`, {
      method: "POST",
      signal: AbortSignal.timeout(5000),
    });

    if (!res.ok) {
      const text = await res.text();
      let error = text || "Failed to trigger scan";
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
