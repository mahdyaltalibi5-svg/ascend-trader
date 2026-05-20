"use client";

import Link from "next/link";
import { useState, useCallback, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  BrainCircuit,
  CalendarClock,
  CandlestickChart,
  CheckCircle2,
  Clock,
  DollarSign,
  FileSearch,
  Flame,
  Gauge,
  Layers,
  ListChecks,
  MessageSquareText,
  Radio,
  Route,
  ScanLine,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  Wallet,
  XCircle,
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
import { useRealtime, useRealtimeSignals, useRealtimeTrades } from "@/hooks/useRealtime";
import { createClient } from "@/lib/supabase/client";
import { formatCurrency, pnlColor, cn } from "@/lib/utils";
import type { BotLog, ScanEvent, Signal } from "@/types";

const WATCHLIST = ["SPY", "NVDA", "AAPL", "TSLA", "MSFT", "AMZN"];

const INTELLIGENCE_LAYERS = [
  { label: "Market Regime", detail: "SPY + QQQ context", icon: Route, tone: "text-cyan" },
  { label: "Technicals", detail: "5m / 1h / daily stack", icon: CandlestickChart, tone: "text-success" },
  { label: "Claude Scoring", detail: "7-criteria decision", icon: BrainCircuit, tone: "text-purple" },
  { label: "Risk Engine", detail: "Heat + correlation gates", icon: ShieldCheck, tone: "text-primary" },
  { label: "Earnings", detail: "Priority catalyst scan", icon: CalendarClock, tone: "text-amber-300" },
  { label: "13F Smart Money", detail: "Lagged fund positioning", icon: FileSearch, tone: "text-cyan" },
  { label: "Form 4 Insiders", detail: "Open-market buying", icon: Wallet, tone: "text-success" },
  { label: "Options Flow", detail: "Unusual calls / puts", icon: Activity, tone: "text-purple" },
  { label: "Short Interest", detail: "Squeeze pressure", icon: Flame, tone: "text-amber-300" },
  { label: "Signal Memory", detail: "Outcome calibration", icon: ListChecks, tone: "text-primary" },
];

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

function logTone(level: BotLog["level"]) {
  if (level === "error") return "text-danger";
  if (level === "warning") return "text-amber-300";
  return "text-cyan";
}

function logIcon(level: BotLog["level"]) {
  if (level === "error") return XCircle;
  if (level === "warning") return ShieldCheck;
  return CheckCircle2;
}

function eventTone(event: ScanEvent) {
  if (event.stage === "error" || event.risk_status === "rejected") return "text-danger";
  if (event.stage === "rejected" || event.action === "veto") return "text-amber-300";
  if (event.stage === "ordered" || event.stage === "accepted") return "text-success";
  if (event.stage === "started") return "text-cyan";
  return "text-primary";
}

function eventIcon(event: ScanEvent) {
  if (event.stage === "error" || event.risk_status === "rejected") return XCircle;
  if (event.stage === "rejected" || event.action === "veto") return AlertTriangle;
  if (event.stage === "ordered" || event.stage === "accepted") return CheckCircle2;
  return ScanLine;
}

function eventTitle(event: ScanEvent) {
  const action = event.action ? event.action.toUpperCase() : event.stage.toUpperCase();
  return `${event.symbol} ${action}`;
}

function eventSubtitle(event: ScanEvent) {
  if (event.rejection_reason) return event.rejection_reason;
  const payload = event.payload ?? {};
  const reasoning = typeof payload.reasoning === "string" ? payload.reasoning : "";
  const catalyst = typeof payload.catalyst_note === "string" ? payload.catalyst_note : "";
  return reasoning || catalyst || `${event.stage} stage recorded`;
}

export default function DashboardPage() {
  const { portfolio, snapshots, loading: portfolioLoading } = usePortfolio();
  const { trades, loading: tradesLoading, refetch: refetchTrades } = useTrades({ status: "open", limit: 8 });
  const [signals, setSignals] = useState<Signal[]>([]);
  const [scanEvents, setScanEvents] = useState<ScanEvent[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<ScanEvent | null>(null);
  const [scanEventsAvailable, setScanEventsAvailable] = useState(true);
  const [botLogs, setBotLogs] = useState<BotLog[]>([]);
  const [sigLoading, setSigLoading] = useState(true);
  const [logsLoading, setLogsLoading] = useState(true);
  const [marketSymbol, setMarketSymbol] = useState(WATCHLIST[0]);
  const [clock, setClock] = useState("--:--");

  const supabase = createClient();

  useEffect(() => {
    setClock(formatClock());
    const timer = window.setInterval(() => setClock(formatClock()), 30000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    void supabase
      .from("signals")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(6)
      .then(({ data }) => {
        setSignals((data as Signal[]) ?? []);
        setSigLoading(false);
      });
  }, []);

  useEffect(() => {
    void supabase
      .from("scan_events")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(10)
      .then(({ data, error }) => {
        if (error) {
          setScanEventsAvailable(false);
        } else {
          const events = (data as ScanEvent[]) ?? [];
          setScanEvents(events);
          setSelectedEvent(events[0] ?? null);
        }
        setLogsLoading(false);
      });
  }, []);

  useEffect(() => {
    if (scanEventsAvailable) return;
    setLogsLoading(true);
    void supabase
      .from("bot_logs")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(8)
      .then(({ data }) => {
        setBotLogs((data as BotLog[]) ?? []);
        setLogsLoading(false);
      });
  }, [scanEventsAvailable]);

  const handleNewTrade = useCallback(() => refetchTrades(), [refetchTrades]);
  const handleNewSignal = useCallback((payload: Record<string, unknown>) => {
    setSignals((prev) => [payload.new as Signal, ...prev].slice(0, 6));
  }, []);
  const handleNewLog = useCallback((payload: Record<string, unknown>) => {
    setBotLogs((prev) => [payload.new as BotLog, ...prev].slice(0, 8));
  }, []);
  const handleNewScanEvent = useCallback((payload: Record<string, unknown>) => {
    const event = payload.new as ScanEvent;
    setScanEventsAvailable(true);
    setScanEvents((prev) => [event, ...prev].slice(0, 10));
    setSelectedEvent((current) => current ?? event);
  }, []);

  useRealtimeTrades(handleNewTrade);
  useRealtimeSignals(handleNewSignal);
  useRealtime({ table: "scan_events", event: "INSERT", onData: handleNewScanEvent });
  useRealtime({ table: "bot_logs", event: "INSERT", onData: handleNewLog });

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

  const rejectedSignals = signals.filter((signal) => signal.signal === "hold").length;
  const acceptedEvents = scanEvents.filter((event) => event.stage === "accepted" || event.stage === "ordered").length;
  const rejectedEvents = scanEvents.filter((event) => event.stage === "rejected" || event.action === "veto").length;
  const topWatchlist = WATCHLIST.map((symbol) => ({
    symbol,
    signal: signals.find((item) => item.symbol === symbol),
  }));

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

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-12">
          <section className="glass overflow-hidden rounded-2xl xl:col-span-7">
            <div className="flex flex-col gap-3 border-b border-white/[0.06] px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="mb-1 flex items-center gap-2">
                  <ScanLine className="h-4 w-4 text-cyan" />
                  <h2 className="font-space text-sm font-semibold text-primary">Bot Activity Tape</h2>
                </div>
                <p className="text-xs text-muted">
                  {scanEventsAvailable
                    ? "Structured scan events: analyzed, accepted, rejected, ordered, and errors"
                    : "Text bot logs until scan_events migration is applied"}
                </p>
              </div>
              <div className="flex items-center gap-2 rounded-lg border border-white/[0.08] bg-base/50 px-3 py-2">
                <span className="h-1.5 w-1.5 rounded-full bg-cyan animate-pulse-slow" />
                <span className="font-space text-[10px] font-semibold uppercase tracking-widest text-muted">
                  {scanEventsAvailable ? "Scan Events" : "Bot Logs"}
                </span>
              </div>
            </div>

            <div className="max-h-[354px] overflow-y-auto">
              {logsLoading ? (
                <div className="space-y-3 p-5">
                  {Array.from({ length: 5 }).map((_, index) => (
                    <div key={index} className="flex gap-3">
                      <div className="shimmer h-8 w-8 rounded-lg" />
                      <div className="flex-1 space-y-2">
                        <div className="shimmer h-3 w-2/3 rounded" />
                        <div className="shimmer h-3 w-1/3 rounded" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : scanEventsAvailable && scanEvents.length === 0 ? (
                <EmptyState
                  icon={MessageSquareText}
                  title="No scan events yet"
                  detail="Once the Railway bot runs a scan, every symbol decision will stream here."
                />
              ) : scanEventsAvailable ? (
                <div className="divide-y divide-white/[0.04]">
                  {scanEvents.map((event, index) => {
                    const Icon = eventIcon(event);
                    const active = selectedEvent?.id === event.id;
                    return (
                      <button
                        key={event.id}
                        onClick={() => setSelectedEvent(event)}
                        className={cn(
                          "grid w-full grid-cols-[36px_1fr_86px] gap-3 px-5 py-3.5 text-left transition-colors hover:bg-white/[0.02]",
                          active && "bg-cyan/[0.04]"
                        )}
                      >
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/[0.06] bg-white/[0.03]">
                          <Icon className={cn("h-4 w-4", eventTone(event))} />
                        </div>
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-space text-sm font-bold text-primary">{eventTitle(event)}</p>
                            <span className="rounded-md border border-white/[0.06] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-widest text-muted">
                              {event.stage}
                            </span>
                          </div>
                          <p className="mt-1 line-clamp-2 text-xs text-muted">{eventSubtitle(event)}</p>
                          <div className="mt-2 flex flex-wrap gap-2">
                            <TinyPill label="Conf" value={formatConfidence(event.confidence)} />
                            <TinyPill label="Setup" value={event.setup_type ?? "--"} />
                            <TinyPill label="Cat" value={event.catalyst_score != null ? `${event.catalyst_score.toFixed(1)}` : "--"} />
                          </div>
                        </div>
                        <span className="pt-1 text-right text-[10px] text-muted">
                          {formatRelativeTime(event.created_at)}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : botLogs.length === 0 ? (
                <EmptyState
                  icon={MessageSquareText}
                  title="No bot activity yet"
                  detail="Once Railway is connected and the scanner runs, activity will stream here."
                />
              ) : (
                <div className="divide-y divide-white/[0.04]">
                  {botLogs.map((log, index) => {
                    const Icon = logIcon(log.level);
                    return (
                      <motion.div
                        key={log.id}
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: index * 0.03 }}
                        className="grid grid-cols-[36px_1fr_74px] gap-3 px-5 py-3.5 transition-colors hover:bg-white/[0.02]"
                      >
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/[0.06] bg-white/[0.03]">
                          <Icon className={cn("h-4 w-4", logTone(log.level))} />
                        </div>
                        <div className="min-w-0">
                          <p className="line-clamp-2 text-sm text-primary">{log.message}</p>
                          <p className="mt-1 font-space text-[10px] uppercase tracking-widest text-muted">
                            {log.level}
                          </p>
                        </div>
                        <span className="pt-1 text-right text-[10px] text-muted">
                          {formatRelativeTime(log.created_at)}
                        </span>
                      </motion.div>
                    );
                  })}
                </div>
              )}
            </div>
          </section>

          <section className="glass rounded-2xl p-5 xl:col-span-5">
            <div className="mb-5 flex items-center justify-between">
              <div>
                <div className="mb-1 flex items-center gap-2">
                  <BrainCircuit className="h-4 w-4 text-purple" />
                  <h2 className="font-space text-sm font-semibold text-primary">Intelligence Stack</h2>
                </div>
                <p className="text-xs text-muted">The signal inputs feeding every Claude decision</p>
              </div>
              <span className="rounded-lg border border-purple/20 bg-purple/10 px-2.5 py-1 font-space text-[10px] font-bold text-purple">
                10 layers
              </span>
            </div>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {INTELLIGENCE_LAYERS.map((layer) => (
                <div
                  key={layer.label}
                  className="flex min-h-[72px] items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.025] p-3"
                >
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/[0.06] bg-base/60">
                    <layer.icon className={cn("h-4 w-4", layer.tone)} />
                  </div>
                  <div className="min-w-0">
                    <p className="truncate font-space text-xs font-bold text-primary">{layer.label}</p>
                    <p className="mt-1 truncate text-[11px] text-muted">{layer.detail}</p>
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-5 rounded-xl border border-white/[0.06] bg-base/50 p-4">
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <ListChecks className="h-4 w-4 text-success" />
                  <p className="font-space text-xs font-bold text-primary">Decision Quality</p>
                </div>
                <span className="font-space text-[10px] text-muted">{signals.length} recent signals</span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <SignalMetric label="Passed" value={scanEventsAvailable ? String(acceptedEvents) : String(signalStats.actionable)} />
                <SignalMetric label="Rejected" value={scanEventsAvailable ? String(rejectedEvents) : String(rejectedSignals)} />
                <SignalMetric label="Avg Conf" value={formatConfidence(signalStats.avgConfidence)} />
              </div>
            </div>

            {selectedEvent && (
              <div className="mt-4 rounded-xl border border-white/[0.06] bg-base/50 p-4">
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div>
                    <p className="font-space text-xs font-bold text-primary">
                      {selectedEvent.symbol} Decision Detail
                    </p>
                    <p className="mt-1 text-xs text-muted">
                      {selectedEvent.scan_id}
                    </p>
                  </div>
                  <span className={cn("font-space text-[10px] font-bold uppercase tracking-widest", eventTone(selectedEvent))}>
                    {selectedEvent.risk_status ?? selectedEvent.stage}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <SignalMetric label="Action" value={selectedEvent.action ?? "--"} />
                  <SignalMetric label="Score" value={selectedEvent.composite_score != null ? selectedEvent.composite_score.toFixed(2) : "--"} />
                  <SignalMetric label="Setup Q" value={selectedEvent.setup_quality != null ? selectedEvent.setup_quality.toFixed(2) : "--"} />
                  <SignalMetric label="RS" value={selectedEvent.rs_signal ?? "--"} />
                </div>
                <p className="mt-3 line-clamp-4 text-xs leading-relaxed text-muted">
                  {eventSubtitle(selectedEvent)}
                </p>
              </div>
            )}
          </section>
        </div>

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
                <Flame className="h-4 w-4 text-success" />
                <h2 className="font-space text-sm font-semibold text-primary">Watchlist Intelligence Matrix</h2>
              </div>
              <p className="text-xs text-muted">Fast read on what the bot has recently seen across priority symbols</p>
            </div>
            <Link
              href="/signals"
              className="flex h-9 w-fit items-center gap-2 rounded-lg border border-white/[0.08] bg-surface px-3 text-xs font-semibold text-muted transition-colors hover:border-cyan/30 hover:text-primary"
            >
              Full signal grid
              <ArrowUpRight className="h-3 w-3" />
            </Link>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {topWatchlist.map(({ symbol, signal }) => {
              const confidence = signal?.confidence ?? signal?.strength ?? null;
              const active = marketSymbol === symbol;
              return (
                <button
                  key={symbol}
                  onClick={() => setMarketSymbol(symbol)}
                  className={cn(
                    "group rounded-xl border p-3 text-left transition-colors",
                    active
                      ? "border-cyan/30 bg-cyan/[0.06]"
                      : "border-white/[0.06] bg-white/[0.025] hover:border-white/[0.12] hover:bg-white/[0.04]"
                  )}
                >
                  <div className="mb-3 flex items-start justify-between">
                    <div>
                      <p className="font-space text-sm font-bold text-primary">{symbol}</p>
                      <p className="mt-0.5 text-[10px] uppercase tracking-widest text-muted">
                        {signal ? formatRelativeTime(signal.created_at) : "No scan"}
                      </p>
                    </div>
                    {signal ? (
                      <TradeBadge signal={signal.signal.toUpperCase() as "BUY" | "SELL" | "HOLD"} />
                    ) : (
                      <span className="rounded-md border border-white/[0.06] px-1.5 py-0.5 text-[9px] font-semibold text-muted">
                        WAIT
                      </span>
                    )}
                  </div>
                  <div className="mb-2 flex items-center justify-between text-[10px] text-muted">
                    <span>Conviction</span>
                    <span className="font-space text-primary">{formatConfidence(confidence)}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                    <div
                      className={cn(
                        "h-full rounded-full",
                        signal?.signal === "sell" ? "bg-danger" : signal?.signal === "buy" ? "bg-cyan" : "bg-muted"
                      )}
                      style={{ width: `${confidence != null ? clampPct(confidence * 100) : 8}%` }}
                    />
                  </div>
                </button>
              );
            })}
          </div>
        </section>

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

function TinyPill({ label, value }: { label: string; value: string }) {
  return (
    <span className="rounded-md border border-white/[0.06] bg-base/50 px-1.5 py-0.5 text-[9px] text-muted">
      {label}: <span className="font-space font-semibold text-primary">{value}</span>
    </span>
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
