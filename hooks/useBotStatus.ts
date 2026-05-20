"use client";

import { useState, useEffect } from "react";
import type { BotStatus } from "@/types";

type BotStatusType = "running" | "stopped" | "error";

export function useBotStatus() {
  const [status, setStatus]   = useState<BotStatusType>("stopped");
  const [lastScan, setLastScan] = useState<string | null>(null);
  const [bot, setBot] = useState<BotStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const check = async () => {
      try {
        const res = await fetch("/api/bot/status", {
          signal: AbortSignal.timeout(2500),
        });
        if (res.ok) {
          const data = (await res.json()) as BotStatus;
          setBot(data);
          setStatus(data.error ? "error" : data.running ? "running" : "stopped");
          if (data.last_scan_at ?? data.last_signal_at) {
            const d = new Date(data.last_scan_at ?? data.last_signal_at!);
            setLastScan(d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
          }
        } else {
          setBot(null);
          setStatus("error");
        }
      } catch {
        setBot(null);
        setStatus("stopped");
      } finally {
        setLoading(false);
      }
    };

    check();
    const id = setInterval(check, 30_000);
    return () => clearInterval(id);
  }, []);

  return { status, lastScan, bot, loading };
}
