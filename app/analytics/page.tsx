"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  TrendingUp,
  TrendingDown,
  BarChart3,
  Zap,
  Target,
  Award,
} from "lucide-react";
import { TradeBadge } from "@/components/ui/TradeBadge";
import { Skeleton } from "@/components/ui/LoadingSkeleton";
import { cn, formatCurrency, formatPercent, pnlColor } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Module-level fetch — starts immediately on module load, survives Strict Mode
// ---------------------------------------------------------------------------
// We kick off the fetch at module-load time so it's in-flight before any
// component mounts. The promise is shared across all (re)mounts.
const _analyticsFetch: Promise<AnalyticsData> =
  typeof window !== "undefined"
    ? fetch("/api/analytics").then((r) => r.json())
    : Promise.resolve({} as AnalyticsData);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface AnalyticsData {
  total_return_pct: number;
  day_pnl_pct: number;
  win_rate: number;
  avg_win_r: number;
  avg_loss_r: number;
  profit_factor: number;
  total_trades: number;
  wins_count: number;
  losses_count: number;
  best_trade: { symbol: string; pnl: number; side: string } | null;
  worst_trade: { symbol: string; pnl: number; side: string } | null;
  sharpe: number;
  max_drawdown_pct: number;
  alpha_pct: number;
  signals_fired: number;
  signal_accuracy: number;
  by_sector: Record<string, { trades: number; wins: number; total_pnl: number }>;
  equity_curve: { date: string; equity: number }[];
  daily_pnl_series: { date: string; pnl_pct: number }[];
  top_trades: { symbol: string; pnl: number | null; side: string }[];
}

// ---------------------------------------------------------------------------
// Animation variants
// ---------------------------------------------------------------------------
const fadeUp = {
  hidden: { opacity: 0, y: 24 },
  show:   { opacity: 1, y: 0  },
};

const stagger = {
  show: { transition: { staggerChildren: 0.08 } },
};

// ---------------------------------------------------------------------------
// Metric card
// ---------------------------------------------------------------------------
function MetricCard({
  label,
  value,
  sub,
  accent,
  icon: Icon,
  index,
  loading,
}: {
  label: string;
  value: string;
  sub?: string;
  accent: "cyan" | "success" | "danger" | "purple";
  icon: React.ComponentType<{ className?: string }>;
  index: number;
  loading: boolean;
}) {
  const colors = {
    cyan:    { ring: "ring-cyan/15",    icon: "text-cyan",    bg: "from-cyan/[0.07]",    glow: "rgba(0,212,255,0.15)"    },
    success: { ring: "ring-success/15", icon: "text-success", bg: "from-success/[0.07]", glow: "rgba(0,255,136,0.15)"    },
    danger:  { ring: "ring-danger/15",  icon: "text-danger",  bg: "from-danger/[0.07]",  glow: "rgba(255,59,92,0.15)"    },
    purple:  { ring: "ring-purple/15",  icon: "text-purple",  bg: "from-purple/[0.07]",  glow: "rgba(124,58,237,0.15)"   },
  };
  const c = colors[accent];

  if (loading) {
    return (
      <div className="glass rounded-2xl p-5">
        <Skeleton className="mb-3 h-3 w-20" />
        <Skeleton className="h-9 w-28" />
        <Skeleton className="mt-2 h-3 w-16" />
      </div>
    );
  }

  return (
    <motion.div
      variants={fadeUp}
      className={cn(
        "glass relative overflow-hidden rounded-2xl p-5",
        `bg-gradient-to-br ${c.bg} to-transparent`
      )}
    >
      {/* Corner glow */}
      <div
        className="pointer-events-none absolute -right-6 -top-6 h-20 w-20 rounded-full blur-2xl"
        style={{ background: c.glow }}
      />

      <div className="mb-4 flex items-start justify-between">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted">
          {label}
        </span>
        <div className={cn("rounded-lg p-1.5 ring-1 bg-white/[0.03]", c.ring)}>
          <Icon className={cn("h-3.5 w-3.5", c.icon)} />
        </div>
      </div>

      <p className="font-space text-2xl font-bold text-primary">{value}</p>
      {sub && <p className="mt-1 text-xs text-muted">{sub}</p>}
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// SVG Equity Curve
// ---------------------------------------------------------------------------
function EquityCurve({
  data,
  loading,
}: {
  data: { date: string; equity: number }[];
  loading: boolean;
}) {
  const HEIGHT = 200;
  const WIDTH  = 900; // viewBox — scales to container

  if (loading) {
    return <div className="shimmer w-full rounded-xl" style={{ height: HEIGHT }} />;
  }

  if (data.length < 2) {
    return (
      <div
        className="flex items-center justify-center text-sm text-muted"
        style={{ height: HEIGHT }}
      >
        Not enough data
      </div>
    );
  }

  const minEq = Math.min(...data.map((d) => d.equity));
  const maxEq = Math.max(...data.map((d) => d.equity));
  const range  = maxEq - minEq || 1;
  const pad    = 12;

  const points = data.map((d, i) => {
    const x = (i / (data.length - 1)) * WIDTH;
    const y = HEIGHT - pad - ((d.equity - minEq) / range) * (HEIGHT - pad * 2);
    return { x, y };
  });

  const pathD =
    points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  const areaD =
    pathD +
    ` L ${WIDTH} ${HEIGHT} L 0 ${HEIGHT} Z`;

  const isPositive = data[data.length - 1].equity >= data[0].equity;
  const lineColor  = isPositive ? "#00d4ff" : "#ff3b5c";
  const gradId     = `equity-grad-${isPositive ? "up" : "down"}`;

  return (
    <div className="relative w-full overflow-hidden rounded-xl" style={{ height: HEIGHT }}>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="h-full w-full"
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor={lineColor} stopOpacity="0.35" />
            <stop offset="100%" stopColor={lineColor} stopOpacity="0.02" />
          </linearGradient>
          <filter id="glow-line">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>

        {/* Area fill */}
        <path d={areaD} fill={`url(#${gradId})`} />

        {/* Line */}
        <path
          d={pathD}
          fill="none"
          stroke={lineColor}
          strokeWidth="2"
          filter="url(#glow-line)"
        />

        {/* Start / end dots */}
        <circle
          cx={points[0].x}
          cy={points[0].y}
          r="3"
          fill={lineColor}
          opacity="0.6"
        />
        <circle
          cx={points[points.length - 1].x}
          cy={points[points.length - 1].y}
          r="4"
          fill={lineColor}
        />
      </svg>

      {/* Y-axis labels */}
      <span className="absolute left-2 top-2 font-space text-[10px] text-muted">
        {formatCurrency(maxEq, 0)}
      </span>
      <span className="absolute bottom-2 left-2 font-space text-[10px] text-muted">
        {formatCurrency(minEq, 0)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Circular progress arc (Signal accuracy)
// ---------------------------------------------------------------------------
function CircleArc({ pct, loading }: { pct: number; loading: boolean }) {
  const R      = 54;
  const STROKE = 8;
  const SIZE   = (R + STROKE) * 2;
  const CIRC   = 2 * Math.PI * R;
  const dash   = (pct / 100) * CIRC;

  if (loading) {
    return (
      <div className="flex items-center justify-center">
        <div className="shimmer h-32 w-32 rounded-full" />
      </div>
    );
  }

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: SIZE, height: SIZE }}>
      <svg width={SIZE} height={SIZE} className="-rotate-90">
        {/* Track */}
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={R}
          fill="none"
          stroke="rgba(255,255,255,0.06)"
          strokeWidth={STROKE}
        />
        {/* Progress */}
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={R}
          fill="none"
          stroke="#00d4ff"
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${CIRC}`}
          style={{ filter: "drop-shadow(0 0 6px rgba(0,212,255,0.6))" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-space text-2xl font-bold text-primary">
          {pct.toFixed(0)}%
        </span>
        <span className="text-[10px] text-muted">accuracy</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Daily PnL Heatmap (GitHub-style)
// ---------------------------------------------------------------------------
function PnlHeatmap({
  data,
  loading,
}: {
  data: { date: string; pnl_pct: number }[];
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="grid gap-1" style={{ gridTemplateColumns: "repeat(13, 1fr)" }}>
        {Array.from({ length: 91 }).map((_, i) => (
          <div key={i} className="shimmer aspect-square rounded-sm" />
        ))}
      </div>
    );
  }

  // Build a map for O(1) lookup
  const map: Record<string, number> = {};
  for (const d of data) map[d.date] = d.pnl_pct;

  // Fill last 91 days (13 weeks × 7 = 91)
  const days: { date: string; pnl_pct: number | null }[] = [];
  for (let i = 90; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    days.push({ date: key, pnl_pct: map[key] ?? null });
  }

  function cellColor(pnl: number | null): string {
    if (pnl === null) return "bg-white/[0.04]";
    if (pnl > 1.5)  return "bg-success opacity-90";
    if (pnl > 0.5)  return "bg-success/60";
    if (pnl > 0)    return "bg-success/30";
    if (pnl < -1.5) return "bg-danger opacity-90";
    if (pnl < -0.5) return "bg-danger/60";
    if (pnl < 0)    return "bg-danger/30";
    return "bg-white/[0.08]";
  }

  // Group into weeks (columns)
  const weeks: typeof days[] = [];
  for (let i = 0; i < days.length; i += 7) {
    weeks.push(days.slice(i, i + 7));
  }

  return (
    <div className="flex gap-1 overflow-x-auto pb-1">
      {weeks.map((week, wi) => (
        <div key={wi} className="flex flex-col gap-1">
          {week.map((day) => (
            <div
              key={day.date}
              title={`${day.date}: ${day.pnl_pct !== null ? formatPercent(day.pnl_pct) : "no data"}`}
              className={cn(
                "h-3.5 w-3.5 rounded-sm transition-opacity hover:opacity-80 cursor-default",
                cellColor(day.pnl_pct)
              )}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sector Bar Chart (pure CSS)
// ---------------------------------------------------------------------------
function SectorBars({
  data,
  loading,
}: {
  data: Record<string, { trades: number; wins: number; total_pnl: number }>;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="space-y-1.5">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-4 w-full rounded-full" />
          </div>
        ))}
      </div>
    );
  }

  const entries = Object.entries(data).sort((a, b) => b[1].total_pnl - a[1].total_pnl);
  if (entries.length === 0) {
    return <p className="text-sm text-muted">No sector data yet</p>;
  }

  const maxAbs = Math.max(...entries.map(([, v]) => Math.abs(v.total_pnl)), 1);

  return (
    <div className="space-y-4">
      {entries.map(([sector, stats]) => {
        const pct     = (Math.abs(stats.total_pnl) / maxAbs) * 100;
        const isPos   = stats.total_pnl >= 0;
        const barColor = isPos ? "bg-cyan" : "bg-danger";

        return (
          <div key={sector}>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-xs font-medium text-primary">{sector}</span>
              <div className="flex items-center gap-3">
                <span className="text-[10px] text-muted">{stats.trades} trades</span>
                <span className={cn("font-space text-xs font-semibold", pnlColor(stats.total_pnl))}>
                  {formatCurrency(stats.total_pnl)}
                </span>
              </div>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-white/[0.05]">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.8, ease: "easeOut" }}
                className={cn("h-full rounded-full", barColor)}
                style={isPos ? { boxShadow: "0 0 8px rgba(0,212,255,0.4)" } : { boxShadow: "0 0 8px rgba(255,59,92,0.4)" }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------
export default function AnalyticsPage() {
  const [data, setData]       = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    _analyticsFetch
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const d = data;

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* ── Header ── */}
      <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-4">
        <div>
          <h1 className="font-space text-lg font-semibold text-primary">Analytics</h1>
          <p className="text-xs text-muted">Performance Intelligence</p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-surface px-3 py-1.5">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-cyan" />
          </span>
          <span className="font-space text-xs font-semibold text-cyan">Live</span>
        </div>
      </div>

      <div className="flex-1 space-y-6 p-6">

        {/* ── Top 6 metric cards ── */}
        <motion.div
          variants={stagger}
          initial="hidden"
          animate="show"
          className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6"
        >
          <MetricCard
            label="Total Return"
            value={loading ? "—" : formatPercent(d?.total_return_pct ?? 0)}
            sub="vs $100k start"
            accent={(d?.total_return_pct ?? 0) >= 0 ? "cyan" : "danger"}
            icon={TrendingUp}
            index={0}
            loading={loading}
          />
          <MetricCard
            label="Sharpe Ratio"
            value={loading ? "—" : (d?.sharpe ?? 0).toFixed(2)}
            sub="risk-adjusted return"
            accent="purple"
            icon={BarChart3}
            index={1}
            loading={loading}
          />
          <MetricCard
            label="Max Drawdown"
            value={loading ? "—" : `-${(d?.max_drawdown_pct ?? 0).toFixed(2)}%`}
            sub="peak-to-trough"
            accent="danger"
            icon={TrendingDown}
            index={2}
            loading={loading}
          />
          <MetricCard
            label="Win Rate"
            value={loading ? "—" : `${(d?.win_rate ?? 0).toFixed(1)}%`}
            sub={`${d?.total_trades ?? 0} trades`}
            accent={(d?.win_rate ?? 0) >= 50 ? "success" : "danger"}
            icon={Target}
            index={3}
            loading={loading}
          />
          <MetricCard
            label="Profit Factor"
            value={loading ? "—" : (d?.profit_factor ?? 0).toFixed(2)}
            sub="wins ÷ losses"
            accent={(d?.profit_factor ?? 0) >= 1.5 ? "success" : (d?.profit_factor ?? 0) >= 1 ? "cyan" : "danger"}
            icon={Zap}
            index={4}
            loading={loading}
          />
          <MetricCard
            label="Alpha vs SPY"
            value={loading ? "—" : formatPercent(d?.alpha_pct ?? 0)}
            sub="over 12% SPY est."
            accent={(d?.alpha_pct ?? 0) >= 0 ? "success" : "danger"}
            icon={Award}
            index={5}
            loading={loading}
          />
        </motion.div>

        {/* ── Equity Curve ── */}
        <motion.div
          variants={fadeUp}
          initial="hidden"
          animate="show"
          transition={{ delay: 0.5 }}
          className="glass rounded-2xl p-5"
        >
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="font-space text-sm font-semibold text-primary">Equity Curve</h2>
              <p className="text-xs text-muted">Last 90 days • Portfolio value</p>
            </div>
            {d && d.equity_curve.length > 0 && (
              <span className={cn(
                "font-space text-sm font-bold",
                (d.total_return_pct ?? 0) >= 0 ? "text-cyan" : "text-danger"
              )}>
                {formatPercent(d.total_return_pct ?? 0)} total
              </span>
            )}
          </div>
          <EquityCurve data={d?.equity_curve ?? []} loading={loading} />
        </motion.div>

        {/* ── Win/Loss + R-Multiples ── */}
        <motion.div
          variants={stagger}
          initial="hidden"
          animate="show"
          transition={{ delayChildren: 0.55 }}
          className="grid grid-cols-1 gap-4 lg:grid-cols-2"
        >
          {/* Win / Loss split */}
          <motion.div variants={fadeUp} className="glass rounded-2xl p-6">
            <h2 className="mb-5 font-space text-sm font-semibold text-primary">Win / Loss Breakdown</h2>
            <div className="mb-6 flex items-stretch divide-x divide-white/[0.06]">
              <div className="flex flex-1 flex-col items-center justify-center gap-1 pr-6">
                {loading
                  ? <Skeleton className="h-14 w-20" />
                  : <span className="font-space text-5xl font-black text-success">
                      {d?.wins_count ?? 0}
                    </span>
                }
                <span className="text-xs text-muted uppercase tracking-wider">Wins</span>
              </div>
              <div className="flex flex-1 flex-col items-center justify-center gap-1 pl-6">
                {loading
                  ? <Skeleton className="h-14 w-20" />
                  : <span className="font-space text-5xl font-black text-danger">
                      {d?.losses_count ?? 0}
                    </span>
                }
                <span className="text-xs text-muted uppercase tracking-wider">Losses</span>
              </div>
            </div>

            {/* R-multiples */}
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-xl border border-white/[0.06] bg-success/[0.05] p-3">
                <p className="text-[10px] uppercase tracking-widest text-muted">Avg Win R</p>
                {loading
                  ? <Skeleton className="mt-1 h-7 w-16" />
                  : <p className="font-space mt-1 text-xl font-bold text-success">
                      {(d?.avg_win_r ?? 0).toFixed(2)}R
                    </p>
                }
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-danger/[0.05] p-3">
                <p className="text-[10px] uppercase tracking-widest text-muted">Avg Loss R</p>
                {loading
                  ? <Skeleton className="mt-1 h-7 w-16" />
                  : <p className="font-space mt-1 text-xl font-bold text-danger">
                      {(d?.avg_loss_r ?? 0).toFixed(2)}R
                    </p>
                }
              </div>
            </div>
          </motion.div>

          {/* Signal accuracy — Brain Score */}
          <motion.div variants={fadeUp} className="glass rounded-2xl p-6">
            <div className="mb-1 flex items-center justify-between">
              <h2 className="font-space text-sm font-semibold text-primary">Brain Score</h2>
              <span className="flex items-center gap-1.5 rounded-full border border-cyan/20 bg-cyan/[0.07] px-2.5 py-1 text-[10px] font-semibold text-cyan">
                <Zap className="h-3 w-3" /> AI Signal Accuracy
              </span>
            </div>
            <p className="mb-6 text-xs text-muted">
              % of AI signals that moved in the predicted direction
            </p>
            <div className="flex flex-col items-center gap-4">
              <CircleArc pct={d?.signal_accuracy ?? 0} loading={loading} />
              <div className="flex w-full items-center justify-between text-xs text-muted">
                <span>{d?.signals_fired ?? 0} signals fired</span>
                <span>
                  {loading ? "—" : `${((d?.signal_accuracy ?? 0) / 100 * (d?.signals_fired ?? 0)).toFixed(0)} correct`}
                </span>
              </div>
            </div>
          </motion.div>
        </motion.div>

        {/* ── Best Trades table ── */}
        <motion.div
          variants={fadeUp}
          initial="hidden"
          animate="show"
          transition={{ delay: 0.65 }}
          className="glass rounded-2xl"
        >
          <div className="border-b border-white/[0.06] px-5 py-4 flex items-center justify-between">
            <h2 className="font-space text-sm font-semibold text-primary">Best Trades</h2>
            <span className="text-[10px] text-muted uppercase tracking-wider">Top 5 by P&L</span>
          </div>

          {loading ? (
            <div className="divide-y divide-white/[0.04]">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 px-5 py-3.5">
                  <Skeleton className="h-4 w-14" />
                  <Skeleton className="h-5 w-12 rounded" />
                  <Skeleton className="h-4 w-20 ml-auto" />
                </div>
              ))}
            </div>
          ) : d?.top_trades.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <TrendingUp className="mb-3 h-8 w-8 text-muted/30" />
              <p className="text-sm text-muted">No closed trades yet</p>
            </div>
          ) : (
            <div className="divide-y divide-white/[0.04]">
              {d?.top_trades.map((t, i) => (
                <motion.div
                  key={`${t.symbol}-${i}`}
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.7 + i * 0.05 }}
                  className="flex items-center gap-4 px-5 py-3.5 hover:bg-white/[0.02] transition-colors"
                >
                  <span className="w-5 font-space text-xs text-muted">#{i + 1}</span>
                  <span className="w-16 font-space text-sm font-semibold text-primary">
                    {t.symbol}
                  </span>
                  <TradeBadge signal={t.side === "buy" ? "BUY" : "SELL"} />
                  <span className={cn("ml-auto font-space text-sm font-bold", pnlColor(t.pnl ?? 0))}>
                    {t.pnl !== null ? formatCurrency(t.pnl) : "—"}
                  </span>
                </motion.div>
              ))}
            </div>
          )}
        </motion.div>

        {/* ── Daily PnL Heatmap ── */}
        <motion.div
          variants={fadeUp}
          initial="hidden"
          animate="show"
          transition={{ delay: 0.75 }}
          className="glass rounded-2xl p-5"
        >
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="font-space text-sm font-semibold text-primary">Daily P&L Heatmap</h2>
              <p className="text-xs text-muted">Last 90 days</p>
            </div>
            <div className="flex items-center gap-2 text-[10px] text-muted">
              <span className="flex items-center gap-1">
                <span className="inline-block h-2.5 w-2.5 rounded-sm bg-danger/60" /> Loss
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-2.5 w-2.5 rounded-sm bg-white/[0.08]" /> Flat
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-2.5 w-2.5 rounded-sm bg-success/60" /> Gain
              </span>
            </div>
          </div>
          <PnlHeatmap data={d?.daily_pnl_series ?? []} loading={loading} />
        </motion.div>

        {/* ── Sector breakdown ── */}
        <motion.div
          variants={fadeUp}
          initial="hidden"
          animate="show"
          transition={{ delay: 0.85 }}
          className="glass rounded-2xl p-5"
        >
          <div className="mb-5 flex items-center justify-between">
            <div>
              <h2 className="font-space text-sm font-semibold text-primary">Sector Breakdown</h2>
              <p className="text-xs text-muted">P&L by sector</p>
            </div>
          </div>
          <SectorBars data={d?.by_sector ?? {}} loading={loading} />
        </motion.div>

      </div>
    </div>
  );
}
