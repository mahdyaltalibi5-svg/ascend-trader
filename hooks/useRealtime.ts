"use client";

import { useEffect, useRef } from "react";
import { createClient } from "@/lib/supabase/client";
import type { RealtimeChannel } from "@supabase/supabase-js";

interface UseRealtimeOptions {
  table: string;
  event?: "INSERT" | "UPDATE" | "DELETE" | "*";
  filter?: string;
  onData: (payload: Record<string, unknown>) => void;
}

export function useRealtime({ table, event = "*", filter, onData }: UseRealtimeOptions) {
  const channelRef = useRef<RealtimeChannel | null>(null);
  const supabase = createClient();

  useEffect(() => {
    const channelName = `realtime:${table}:${event}:${filter ?? "all"}`;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let sub = supabase.channel(channelName).on(
      "postgres_changes" as any,
      { event, schema: "public", table, ...(filter ? { filter } : {}) },
      (payload: Record<string, unknown>) => onData(payload)
    );

    channelRef.current = sub.subscribe();

    return () => {
      supabase.removeChannel(channelRef.current!);
    };
  }, [table, event, filter, supabase, onData]);
}

export function useRealtimeTrades(onData: (payload: Record<string, unknown>) => void) {
  return useRealtime({ table: "trades", onData });
}

export function useRealtimeSignals(onData: (payload: Record<string, unknown>) => void) {
  return useRealtime({ table: "signals", onData });
}

export function useRealtimePortfolio(onData: (payload: Record<string, unknown>) => void) {
  return useRealtime({ table: "portfolio_snapshots", onData });
}
