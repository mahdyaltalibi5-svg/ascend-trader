"use client";

import { useEffect, useState, useCallback } from "react";
import { createClient } from "@/lib/supabase/client";
import type { Trade, TradeStatus } from "@/types";

interface UseTradesOptions {
  status?: TradeStatus | undefined;
  symbol?: string;
  limit?: number;
}

export function useTrades(options: UseTradesOptions = {}) {
  const { status, symbol, limit = 50 } = options;
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const supabase = createClient();

  const fetchTrades = useCallback(async () => {
    try {
      setLoading(true);
      let query = supabase
        .from("trades")
        .select("*")
        .order("created_at", { ascending: false })
        .limit(limit);

      if (status) query = query.eq("status", status);
      if (symbol) query = query.eq("symbol", symbol);

      const { data, error } = await query;
      if (error) throw error;
      setTrades((data as Trade[]) ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch trades");
    } finally {
      setLoading(false);
    }
  }, [supabase, status, symbol, limit]);

  useEffect(() => {
    fetchTrades();
  }, [fetchTrades]);

  return { trades, loading, error, refetch: fetchTrades };
}
