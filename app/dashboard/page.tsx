"use client";

import { useState, useCallback, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  DollarSign,
  TrendingUp,
  Target,
  Layers,
  Clock,
  Zap,
} from "lucide-react";
import { StatCard } from "@/components/ui/StatCard";
import { TradeBadge } from "@/components/ui/TradeBadge";
import { SignalStrengthBar } from "@/components/ui/SignalStrengthBar";
import { ChartSkeleton, TradeRowSkeleton, SignalCardSkeleton } from "@/components/ui/LoadingSkeleton";
import { EquityChart } from "@/components/charts/EquityChart";
import { usePortfolio } from "@/hooks/usePortfolio";
import { useTrades } from "@/hooks/useTrades";
import { useRealtimeSignals, useRealtimeTrades } from "@/hooks/useRealtime";
import { createClient } from "@/lib/supabase/client";
import { formatCurrency, pnlColor, cn } from "@/lib/utils";
import type { Signal } from "@/types";

export default function DashboardPage() {
  const { portfolio, snapshots, loading: portfolioLoading } = usePortfolio();
  const { trades, loading: tradesLoading, refetch: refetchTrades } = useTrades({ status: "open", limit: 8 });
  const [signals, setSignals] = useState<Signal[]>([]);
  const [sigLoading, setSigLoading] = useState(true);

  const supabase = createClient();

  useEffect(() => {
    supabase
      .from("signals")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(6)
      .then(({ data }) => {
        setSignals((data as Signal[]) ?? []);
        setSigLoading(false);
      });
  }, []);

  const handleNewTrade = useCallback(() => refetchTrades(), [refetchTrades]);
  const handleNewSignal = useCallback((payload: Record<string, unknown>) => {
    setSignals((prev) => [payload.new as Signal, ...prev].slice(0, 6));
  }, []);

  useRealtimeTrades(handleNewTrade);
  useRealtimeSignals(handleNewSignal);

  const equity    = portfolio?.equity ?? 0;
  const cash      = portfolio?.cash ?? 0;
  const dayPnl    = portfolio?.day_pnl ?? 0;
  const totalPnl  = portfolio?.total_pnl ?? 0;
  const dayPct    = equity > 0 ? (dayPnl / (equity - dayPnl)) * 100 : 0;

  // Win rate from closed trades (lightweight calc from hook data)
  const [winRate, setWinRate] = useState<number | null>(null);
  useEffect(() => {
    supabase
      .from("trades")
      .select("pnl")
      .eq("status", "closed")
      .then(({ data }) => {
        if (!data || data.length === 0) { setWinRate(null); return; }
        const wins = data.filter((t) => (t.pnl ?? 0) > 0).length;
        setWinRate((wins / data.length) * 100);
      });
  }, []);

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-4">
        <div>
          <h1 className="font-space text-lg font-semibold text-primary">Dashboard</h1>
          <p className="text-xs text-muted">Live portfolio overview</p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-surface px-3 py-1.5">
          <Clock className="h-3 w-3 text-muted" />
          <span className="text-xs text-muted">
            {new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
      </div>

      <div className="flex-1 space-y-6 p-6">
        {/* Stat cards */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard
            label="Total Equity"
            value={formatCurrency(equity)}
            change={dayPct}
            icon={DollarSign}
            accentColor="cyan"
            index={0}
            loading={portfolioLoading}
          />
          <StatCard
            label="Daily P&L"
            value={formatCurrency(dayPnl)}
            change={dayPct}
            icon={TrendingUp}
            accentColor={dayPnl >= 0 ? "success" : "danger"}
            index={1}
            loading={portfolioLoading}
          />
          <StatCard
            label="Win Rate"
            value={winRate !== null ? `${winRate.toFixed(1)}%` : "—"}
            icon={Target}
            accentColor="purple"
            index={2}
            loading={portfolioLoading}
          />
          <StatCard
            label="Open Positions"
            value={String(trades.length)}
            icon={Layers}
            accentColor="cyan"
            index={3}
            loading={portfolioLoading}
          />
        </div>

        {/* Equity curve */}
        <div className="glass rounded-2xl p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="font-space text-sm font-semibold text-primary">Equity Curve</h2>
              <p className="text-xs text-muted">Portfolio value over time</p>
            </div>
            <span className={cn("font-space text-sm font-bold", pnlColor(totalPnl))}>
              {totalPnl >= 0 ? "+" : ""}{formatCurrency(totalPnl)}
            </span>
          </div>
          {portfolioLoading
            ? <ChartSkeleton height={260} />
            : <EquityChart snapshots={snapshots} height={260} />
          }
        </div>

        {/* Bottom split */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
          {/* Recent trades (60%) */}
          <div className="glass rounded-2xl lg:col-span-3">
            <div className="border-b border-white/[0.06] px-5 py-4">
              <h2 className="font-space text-sm font-semibold text-primary">Recent Trades</h2>
            </div>
            <div className="divide-y divide-white/[0.04]">
              {tradesLoading ? (
                Array.from({ length: 4 }).map((_, i) => <TradeRowSkeleton key={i} />)
              ) : trades.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <Layers className="mb-3 h-8 w-8 text-muted/30" />
                  <p className="text-sm text-muted">No open trades</p>
                </div>
              ) : (
                <AnimatePresence>
                  {trades.map((trade, i) => (
                    <motion.div
                      key={trade.id}
                      initial={{ opacity: 0, x: -12 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: i * 0.04 }}
                      className="flex items-center gap-4 px-5 py-3.5 hover:bg-white/[0.02] transition-colors"
                    >
                      <span className="font-space text-sm font-semibold text-primary w-14">
                        {trade.symbol}
                      </span>
                      <TradeBadge signal={trade.side === "buy" ? "BUY" : "SELL"} />
                      <span className="text-xs text-muted">
                        {formatCurrency(trade.entry_price ?? 0)}
                      </span>
                      <span className="ml-auto font-space text-sm font-medium">
                        <span className={pnlColor(trade.pnl ?? 0)}>
                          {trade.pnl !== null ? formatCurrency(trade.pnl) : "—"}
                        </span>
                      </span>
                      <span className="text-[10px] text-muted w-16 text-right">
                        {new Date(trade.entry_at ?? trade.created_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    </motion.div>
                  ))}
                </AnimatePresence>
              )}
            </div>
          </div>

          {/* Active signals (40%) */}
          <div className="glass rounded-2xl lg:col-span-2">
            <div className="border-b border-white/[0.06] px-5 py-4 flex items-center justify-between">
              <h2 className="font-space text-sm font-semibold text-primary">Live Signals</h2>
              <span className="flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-cyan animate-pulse-slow" />
                <span className="text-[10px] text-muted">Live</span>
              </span>
            </div>
            <div className="divide-y divide-white/[0.04]">
              {sigLoading ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="p-4">
                    <SignalCardSkeleton />
                  </div>
                ))
              ) : signals.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <Zap className="mb-3 h-8 w-8 text-muted/30" />
                  <p className="text-sm text-muted">Awaiting signals</p>
                </div>
              ) : (
                <AnimatePresence>
                  {signals.map((sig, i) => (
                    <motion.div
                      key={sig.id}
                      initial={{ opacity: 0, y: -8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: i * 0.04 }}
                      className="px-5 py-4"
                    >
                      <div className="mb-2 flex items-center justify-between">
                        <span className="font-space text-base font-bold text-primary">
                          {sig.symbol}
                        </span>
                        <TradeBadge signal={sig.signal.toUpperCase() as "BUY"|"SELL"|"HOLD"} glow />
                      </div>
                      <SignalStrengthBar strength={sig.strength} signal={sig.signal} />
                    </motion.div>
                  ))}
                </AnimatePresence>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

