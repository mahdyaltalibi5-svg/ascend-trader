"use client";

import { useEffect, useState, useCallback } from "react";
import { createClient } from "@/lib/supabase/client";
import type { Portfolio, PortfolioSnapshot } from "@/types";

export function usePortfolio() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [snapshots, setSnapshots] = useState<PortfolioSnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const supabase = createClient();

  const fetchPortfolio = useCallback(async () => {
    try {
      const { data, error } = await supabase
        .from("portfolio_snapshots")
        .select("*")
        .order("snapshot_at", { ascending: false })
        .limit(1)
        .single();

      if (error) throw error;
      setPortfolio(data as Portfolio);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch portfolio");
    } finally {
      setLoading(false);
    }
  }, [supabase]);

  const fetchSnapshots = useCallback(async (days = 30) => {
    const since = new Date();
    since.setDate(since.getDate() - days);

    const { data } = await supabase
      .from("portfolio_snapshots")
      .select("snapshot_at, equity, cash, daily_pnl, total_pnl")
      .gte("snapshot_at", since.toISOString())
      .order("snapshot_at", { ascending: true });

    setSnapshots((data as PortfolioSnapshot[]) ?? []);
  }, [supabase]);

  useEffect(() => {
    fetchPortfolio();
    fetchSnapshots();
  }, [fetchPortfolio, fetchSnapshots]);

  return { portfolio, snapshots, loading, error, refetch: fetchPortfolio };
}
