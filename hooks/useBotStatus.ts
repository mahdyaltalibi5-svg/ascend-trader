"use client";

import { useState, useEffect } from "react";
import { BOT_API_URL } from "@/lib/constants";

type BotStatusType = "running" | "stopped" | "error";

export function useBotStatus() {
  const [status, setStatus]   = useState<BotStatusType>("stopped");
  const [lastScan, setLastScan] = useState<string | null>(null);

  useEffect(() => {
    const check = async () => {
      try {
        const res = await fetch(`${BOT_API_URL}/status`, {
          signal: AbortSignal.timeout(2500),
        });
        if (res.ok) {
          const data = await res.json();
          setStatus(data.running ? "running" : "stopped");
          if (data.last_signal_at) {
            const d = new Date(data.last_signal_at);
            setLastScan(d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
          }
        } else {
          setStatus("error");
        }
      } catch {
        setStatus("stopped");
      }
    };

    check();
    const id = setInterval(check, 30_000);
    return () => clearInterval(id);
  }, []);

  return { status, lastScan };
}
