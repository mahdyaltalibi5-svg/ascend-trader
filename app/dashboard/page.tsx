"use client";

import Link from "next/link";
import { useState, useCallback, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  BrainCircuit,
  CalendarClock,
  CandlestickChart,
  Clock,
  DollarSign,
  Gauge,
  Layers,
  Radio,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  Wallet,
  Zap,
} from "lucide-react";
import { StatCard } from "@/components/ui/StatCard";
import { TradeBadge } from "@/components/ui/TradeBadge";
import { SignalStrengthBar } from "@/components/ui/SignalStrengthBar";
import { ChartSkeleton, TradeRowSkeleton, SignalCardSkeleton } from "@/components/ui/LoadingSkeleton";
import { EquityChart } from "@/components/charts/EquityChart";
import { TradingViewChart } from "@/components/charts/TradingViewChart";
import { BotCommandCenter } from "@/components/bot/BotCommandCenter";
import { usePortfolio } from "@/hooks/usePortfolio";
import { useTrades } from "@/hooks/useTrades";
import { useRealtimeSignals, useRealtimeTrades } from "@/hooks/useRealtime";
import { createClient } from "@/lib/supabase/client";
import { formatCurrency, pnlColor, cn } from "@/lib/utils";
import type { Signal } from "@/types";

const WATCHLIST = ["SPY", "NVDA", "AAPL", "TSLA", "MSFT", "AMZN"];

const QUICK_LINKS = [
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/backtest", label: "Research", icon: BrainCircuit },
  { href: "/signals", label: "Signals", icon: Radio },
  { href: "/settings", label: "Controls", icon: Gauge },
];

function formatConfidence(value: number | null | undefined) {
  if (value == null) return "--";
  return `${Math.round(value * 100)}%`;
}

function formatRelativeTime(value: string | null | undefined) {
  if (!value) return "Not yet";
  const diffMs = Date.now() - new Date(value).getTime();
  const mins = Math.max(0, Math.floor(diffMs / 60000));
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function clampPct(value: number) {
  return Math.min(100, Math.max(0, value));
}

function formatClock() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function DashboardPage() {
  const { portfolio, snapshots, loading: portfolioLoading } = usePortfolio();
  const { trades, loading: tradesLoading, refetch: refetchTrades } = useTrades({ status: "open", limit: 8 });
  const [signals, setSignals] = useState<Signal[]>([]);
  const [sigLoading, setSigLoading] = useState(true);
  const [marketSymbol, setMarketSymbol] = useState(WATCHLIST[0]);
  const [clock, setClock] = useState("--:--");

  const supabase = createClient();

  useEffect(() => {
    setClock(formatClock());
    const timer = window.setInterval(() => setClock(formatClock()), 30000);
    return () => window.clearInterval(timer);
  }, []);

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

  const equity = portfolio?.equity ?? 0;
  const cash = portfolio?.cash ?? 0;
  const dayPnl = portfolio?.day_pnl ?? 0;
  const totalPnl = portfolio?.total_pnl ?? 0;
  const buyingPower = portfolio?.buying_power ?? cash;
  const dayPct = equity > 0 && equity !== dayPnl ? (dayPnl / (equity - dayPnl)) * 100 : 0;

  const [winRate, setWinRate] = useState<number | null>(null);
  useEffect(() => {
    supabase
      .from("trades")
      .select("pnl")
      .eq("status", "closed")
      .then(({ data }) => {
        if (!data || data.length === 0) {
          setWinRate(null);
          return;
        }
        const wins = data.filter((t) => (t.pnl ?? 0) > 0).length;
        setWinRate((wins / data.length) * 100);
      });
  }, []);

  const signalStats = useMemo(() => {
    const actionable = signals.filter((signal) => signal.signal !== "hold");
    const avgConfidence =
      actionable.length > 0
        ? actionable.reduce((sum, signal) => sum + (signal.confidence ?? signal.strength ?? 0), 0) / actionable.length
        : null;
    const topSignal = signals[0];

    return {
      actionable: actionable.length,
      avgConfidence,
      topSignal,
      lastSignalAt: topSignal?.created_at ?? null,
    };
  }, [signals]);

  const exposurePct = equity > 0 ? clampPct(((equity - cash) / equity) * 100) : 0;
  const cashPct = equity > 0 ? clampPct((cash / equity) * 100) : 0;
  const readinessScore = clampPct(
    55 +
      (signals.length > 0 ? 12 : 0) +
      (trades.length > 0 ? 8 : 0) +
      (dayPnl >= 0 ? 10 : -10) +
      (winRate != null && winRate >= 50 ? 10 : 0) +
      (cashPct >= 20 ? 5 : -5)
  );

  const readinessLabel =
    readinessScore >= 85 ? "Attack Mode" :
    readinessScore >= 70 ? "Ready" :
    readinessScore >= 55 ? "Watching" :
    "Defensive";

  const tacticalItems = [
    {
      label: "Readiness",
      value: readinessLabel,
      meta: `${readinessScore.toFixed(0)}/100`,
      icon: ShieldCheck,
      tone: readinessScore >= 70 ? "text-success" : "text-cyan",
    },
    {
      label: "Signal Pipeline",
      value: `${signalStats.actionable}/${signals.length}`,
      meta: `${formatConfidence(signalStats.avgConfidence)} avg confidence`,
      icon: BrainCircuit,
      tone: "text-purple",
    },
    {
      label: "Live Exposure",
      value: `${exposurePct.toFixed(0)}%`,
      meta: `${trades.length} open positions`,
      icon: Activity,
      tone: exposurePct > 70 ? "text-danger" : "text-cyan",
    },
    {
      label: "Last Signal",
      value: signalStats.topSignal?.symbol ?? "None",
      meta: formatRelativeTime(signalStats.lastSignalAt),
      icon: CalendarClock,
      tone: "text-primary",
    },
  ];

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="border-b border-white/[0.06] px-6 py-5">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2">
              <span className="flex h-2 w-2 rounded-full bg-success shadow-[0_0_18px_rgba(16,185,129,0.7)]" />
              <span className="font-space text-[10px] font-semibold uppercase tracking-[0.24em] text-muted">
                Ascend Command
              </span>
            </div>
            <h1 className="font-space text-2xl font-bold text-primary md:text-3xl">
              Mission Control
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-muted">
              Live portfolio, bot decisions, signal quality, and market context in one operating screen.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {QUICK_LINKS.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="group flex h-10 items-center gap-2 rounded-lg border border-white/[0.08] bg-surface/70 px-3 text-xs font-semibold text-muted transition-colors hover:border-cyan/30 hover:text-primary"
              >
                <item.icon className="h-3.5 w-3.5 text-cyan/80" />
                {item.label}
                <ArrowUpRight className="h-3 w-3 opacity-0 transition-opacity group-hover:opacity-100" />
              </Link>
            ))}
            <div className="flex h-10 items-center gap-2 rounded-lg border border-white/[0.08] bg-surface px-3">
              <Clock className="h-3.5 w-3.5 text-muted" />
              <span className="font-space text-xs text-muted">
                {clock}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 space-y-6 p-6">
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-4">
          {tacticalItems.map((item, index) => (
            <motion.div
              key={item.label}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.05 }}
              className="glass flex min-h-[96px] items-center gap-4 rounded-2xl p-4"
            >
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-white/[0.08] bg-white/[0.03]">
                <item.icon className={cn("h-5 w-5", item.tone)} />
              </div>
              <div className="min-w-0">
                <p className="text-[10px] font-semibold uppercase tracking-widest text-muted">
                  {item.label}
                </p>
                <p className="mt-1 truncate font-space text-lg font-bold text-primary">
                  {item.value}
                </p>
                <p className="truncate text-xs text-muted">{item.meta}</p>
              </div>
            </motion.div>
          ))}
        </div>

        <BotCommandCenter />

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
            value={winRate !== null ? `${winRate.toFixed(1)}%` : "--"}
            icon={Target}
            accentColor="purple"
            index={2}
            loading={portfolioLoading}
          />
          <StatCard
            label="Buying Power"
            value={formatCurrency(buyingPower)}
            icon={Wallet}
            accentColor="cyan"
            index={3}
            loading={portfolioLoading}
          />
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-12">
          <section className="glass rounded-2xl p-5 xl:col-span-7">
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="font-space text-sm font-semibold text-primary">Equity Curve</h2>
                <p className="text-xs text-muted">Account trajectory and realized drift</p>
              </div>
              <div className="flex items-center gap-4">
                <div className="text-right">
                  <p className="text-[10px] uppercase tracking-widest text-muted">Total P&L</p>
                  <p className={cn("font-space text-sm font-bold", pnlColor(totalPnl))}>
                    {totalPnl >= 0 ? "+" : ""}{formatCurrency(totalPnl)}
                  </p>
                </div>
                <div className="h-9 w-px bg-white/[0.08]" />
                <div className="text-right">
                  <p className="text-[10px] uppercase tracking-widest text-muted">Cash</p>
                  <p className="font-space text-sm font-bold text-primary">{cashPct.toFixed(0)}%</p>
                </div>
              </div>
            </div>
            {portfolioLoading
              ? <ChartSkeleton height={300} />
              : <EquityChart snapshots={snapshots} height={300} />
            }
          </section>

          <section className="glass rounded-2xl p-5 xl:col-span-5">
            <div className="mb-5 flex items-center justify-between">
              <div>
                <h2 className="font-space text-sm font-semibold text-primary">Risk Posture</h2>
                <p className="text-xs text-muted">Exposure, liquidity, and decision quality</p>
              </div>
              <Sparkles className="h-4 w-4 text-cyan" />
            </div>

            <div className="space-y-5">
              <div>
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="text-muted">Portfolio exposure</span>
                  <span className="font-space font-semibold text-primary">{exposurePct.toFixed(0)}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className={cn(
                      "h-full rounded-full",
                      exposurePct > 70 ? "bg-danger" : "bg-cyan"
                    )}
                    style={{ width: `${exposurePct}%` }}
                  />
                </div>
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="text-muted">Cash reserve</span>
                  <span className="font-space font-semibold text-primary">{cashPct.toFixed(0)}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-white/[0.06]">
                  <div className="h-full rounded-full bg-success" style={{ width: `${cashPct}%` }} />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <MetricTile label="Open Trades" value={String(trades.length)} />
                <MetricTile label="Signals" value={String(signals.length)} />
                <MetricTile label="Avg Confidence" value={formatConfidence(signalStats.avgConfidence)} />
                <MetricTile label="Day Move" value={`${dayPct >= 0 ? "+" : ""}${dayPct.toFixed(2)}%`} tone={dayPct >= 0 ? "good" : "bad"} />
              </div>
            </div>
          </section>
        </div>

        <section className="glass rounded-2xl p-5">
          <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="mb-1 flex items-center gap-2">
                <CandlestickChart className="h-4 w-4 text-cyan" />
                <h2 className="font-space text-sm font-semibold text-primary">Live Market</h2>
              </div>
              <p className="text-xs text-muted">TradingView chart with the active scan watchlist</p>
            </div>
            <div className="grid grid-cols-3 gap-1.5 sm:flex">
              {WATCHLIST.map((symbol) => (
                <button
                  key={symbol}
                  onClick={() => setMarketSymbol(symbol)}
                  className={cn(
                    "h-8 rounded-lg px-3 font-space text-xs font-semibold transition-colors",
                    marketSymbol === symbol
                      ? "bg-cyan/10 text-cyan ring-1 ring-cyan/25"
                      : "text-muted hover:bg-white/[0.04] hover:text-primary"
                  )}
                >
                  {symbol}
                </button>
              ))}
            </div>
          </div>
          <TradingViewChart symbol={marketSymbol} interval="15" height={430} />
        </section>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
          <section className="glass rounded-2xl lg:col-span-3">
            <div className="flex items-center justify-between border-b border-white/[0.06] px-5 py-4">
              <div>
                <h2 className="font-space text-sm font-semibold text-primary">Open Trades</h2>
                <p className="text-xs text-muted">Positions the bot is actively monitoring</p>
              </div>
              <span className="rounded-lg border border-white/[0.08] px-2 py-1 font-space text-xs text-muted">
                {trades.length} live
              </span>
            </div>
            <div className="divide-y divide-white/[0.04]">
              {tradesLoading ? (
                Array.from({ length: 4 }).map((_, i) => <TradeRowSkeleton key={i} />)
              ) : trades.length === 0 ? (
                <EmptyState
                  icon={Layers}
                  title="No open trades"
                  detail="The bot is waiting for a setup that passes risk checks."
                />
              ) : (
                <AnimatePresence>
                  {trades.map((trade, i) => (
                    <motion.div
                      key={trade.id}
                      initial={{ opacity: 0, x: -12 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: i * 0.04 }}
                      className="grid grid-cols-[72px_70px_1fr_1fr_72px] items-center gap-3 px-5 py-3.5 transition-colors hover:bg-white/[0.02]"
                    >
                      <span className="font-space text-sm font-semibold text-primary">
                        {trade.symbol}
                      </span>
                      <TradeBadge signal={trade.side === "buy" ? "BUY" : "SELL"} />
                      <div className="min-w-0">
                        <p className="text-[10px] uppercase tracking-widest text-muted">Entry</p>
                        <p className="truncate font-space text-xs text-primary">
                          {formatCurrency(trade.entry_price ?? 0)}
                        </p>
                      </div>
                      <div className="min-w-0 text-right">
                        <p className="text-[10px] uppercase tracking-widest text-muted">P&L</p>
                        <p className={cn("truncate font-space text-xs font-semibold", pnlColor(trade.pnl ?? 0))}>
                          {trade.pnl !== null ? formatCurrency(trade.pnl) : "--"}
                        </p>
                      </div>
                      <span className="text-right text-[10px] text-muted">
                        {formatRelativeTime(trade.entry_at ?? trade.created_at)}
                      </span>
                    </motion.div>
                  ))}
                </AnimatePresence>
              )}
            </div>
          </section>

          <section className="glass rounded-2xl lg:col-span-2">
            <div className="flex items-center justify-between border-b border-white/[0.06] px-5 py-4">
              <div>
                <h2 className="font-space text-sm font-semibold text-primary">Live Signals</h2>
                <p className="text-xs text-muted">Newest Claude-scored opportunities</p>
              </div>
              <span className="flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-cyan animate-pulse-slow" />
                <span className="text-[10px] text-muted">Realtime</span>
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
                <EmptyState
                  icon={Zap}
                  title="Awaiting signals"
                  detail="Fresh scan output will appear here as soon as the bot sees a setup."
                />
              ) : (
                <AnimatePresence>
                  {signals.map((signal, i) => (
                    <motion.div
                      key={signal.id}
                      initial={{ opacity: 0, y: -8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: i * 0.04 }}
                      className="px-5 py-4 transition-colors hover:bg-white/[0.02]"
                    >
                      <div className="mb-3 flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-space text-base font-bold text-primary">
                              {signal.symbol}
                            </span>
                            {signal.executed && (
                              <span className="rounded-full bg-success/10 px-2 py-0.5 text-[10px] font-semibold text-success">
                                Executed
                              </span>
                            )}
                          </div>
                          <p className="truncate text-xs text-muted">
                            {signal.strategy} - {formatRelativeTime(signal.created_at)}
                          </p>
                        </div>
                        <TradeBadge signal={signal.signal.toUpperCase() as "BUY" | "SELL" | "HOLD"} glow />
                      </div>

                      <SignalStrengthBar strength={signal.strength} signal={signal.signal} />

                      <div className="mt-3 grid grid-cols-3 gap-2">
                        <SignalMetric label="Conf" value={formatConfidence(signal.confidence ?? signal.strength)} />
                        <SignalMetric label="Rules" value={signal.criteria_met != null ? `${signal.criteria_met}/7` : "--"} />
                        <SignalMetric label="R/R" value={signal.risk_reward_ratio != null ? `${signal.risk_reward_ratio.toFixed(1)}x` : "--"} />
                      </div>
                    </motion.div>
                  ))}
                </AnimatePresence>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function MetricTile({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "good" | "bad";
}) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.025] p-3">
      <p className="text-[10px] uppercase tracking-widest text-muted">{label}</p>
      <p
        className={cn(
          "mt-1 truncate font-space text-sm font-bold",
          tone === "good" ? "text-success" : tone === "bad" ? "text-danger" : "text-primary"
        )}
      >
        {value}
      </p>
    </div>
  );
}

function SignalMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-white/[0.025] px-2 py-2 text-center">
      <p className="text-[9px] uppercase tracking-widest text-muted">{label}</p>
      <p className="mt-1 font-space text-xs font-bold text-primary">{value}</p>
    </div>
  );
}

function EmptyState({
  icon: Icon,
  title,
  detail,
}: {
  icon: typeof Layers;
  title: string;
  detail: string;
}) {
  return (
    <div className="flex min-h-[220px] flex-col items-center justify-center px-6 py-12 text-center">
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-white/[0.06] bg-white/[0.03]">
        <Icon className="h-5 w-5 text-muted/60" />
      </div>
      <p className="text-sm font-semibold text-primary">{title}</p>
      <p className="mt-1 max-w-xs text-xs text-muted">{detail}</p>
    </div>
  );
}
