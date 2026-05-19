import { NextRequest } from "next/server";
import { BOT_API_URL } from "@/lib/constants";

export async function POST(_req: NextRequest) {
  try {
    // Check current status
    const statusRes = await fetch(`${BOT_API_URL}/status`, {
      signal: AbortSignal.timeout(3000),
    });

    if (!statusRes.ok) {
      return Response.json({ error: "Bot API unreachable" }, { status: 502 });
    }

    const status = await statusRes.json();
    const action = status.running ? "stop" : "start";

    const toggleRes = await fetch(`${BOT_API_URL}/${action}`, {
      method: "POST",
      signal: AbortSignal.timeout(5000),
    });

    if (!toggleRes.ok) {
      return Response.json({ error: `Failed to ${action} bot` }, { status: 502 });
    }

    return Response.json({ action, running: !status.running });
  } catch {
    return Response.json({ error: "Bot API not reachable" }, { status: 502 });
  }
}
