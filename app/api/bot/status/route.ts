import { BOT_API_URL } from "@/lib/constants";

export async function GET() {
  try {
    const res = await fetch(`${BOT_API_URL}/status`, {
      signal: AbortSignal.timeout(3500),
      cache: "no-store",
    });

    if (!res.ok) {
      return Response.json({
        running: false,
        strategy: "ascend_elite_v3",
        last_signal_at: null,
        trades_today: 0,
        signals_today: 0,
        win_rate: 0,
        uptime_seconds: 0,
        error: "Bot API unreachable",
      });
    }

    return Response.json(await res.json());
  } catch {
    return Response.json({
      running: false,
      strategy: "ascend_elite_v3",
      last_signal_at: null,
      trades_today: 0,
      signals_today: 0,
      win_rate: 0,
      uptime_seconds: 0,
      error: "Bot API not reachable",
    });
  }
}
