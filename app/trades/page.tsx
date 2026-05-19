"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Search, ChevronDown, Brain, Clock, BarChart2, ArrowLeftRight } from "lucide-react";
import { TradeBadge } from "@/components/ui/TradeBadge";
import { CandleChart } from "@/components/charts/CandleChart";
import { TradeRowSkeleton } from "@/components/ui/LoadingSkeleton";
import { useTrades } from "@/hooks/useTrades";
import { formatCurrency, pnlColor, cn } from "@/lib/utils";
import type { Trade, TradeStatus } from "@/types";

const TABS: { label: string; value: TradeStatus | "all" }[] = [
  { label: "All",    value: "all"    },
  { label: "Open",   value: "open"   },
  { label: "Closed", value: "closed" },
];

function TypewriterText({ text }: { text: string }) {
  const [displayed, setDisplayed] = useState("");
  useEffect(() => {
    setDisplayed("");
    let i = 0;
    const t = setInterval(() => {
      setDisplayed(text.slice(0, ++i));
      if (i >= text.length) clearInterval(t);
    }, 14);
    return () => clearInterval(t);
  }, [text]);
  return (
    <span>
      {displayed}
      <span className="ml-0.5 animate-pulse-slow text-purple">|</span>
    </span>
  );
}

function ExpandedRow({ trade }: { trade: Trade }) {
  const holdMs =
    trade.exit_at && trade.entry_at
      ? new Date(trade.exit_at).getTime() - new Date(trade.entry_at).getTime()
      : null;
  const holdLabel = holdMs
    ? holdMs < 3_600_000
      ? `${Math.round(holdMs / 60000)}m`
      : `${(holdMs / 3_600_000).toFixed(1)}h`
    : "—";

  const rr =
    trade.entry_price && trade.exit_price && trade.stop_loss
      ? Math.abs(trade.exit_price - trade.entry_price) /
        Math.abs(trade.entry_price - trade.stop_loss)
      : null;

  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.25, ease: "easeInOut" }}
      className="overflow-hidden border-b border-white/[0.06] bg-base/50"
    >
      <div className="grid grid-cols-1 gap-6 p-6 lg:grid-cols-2">
        {/* Chart */}
        <div>
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">
            {trade.symbol} Chart
          </p>
          <div className="rounded-xl overflow-hidden border border-white/[0.06]">
            <CandleChart
              candles={[]}
              entryPrice={trade.entry_price ?? undefined}
              exitPrice={trade.exit_price ?? undefined}
              height={220}
            />
          </div>
        </div>

        {/* Details */}
        <div className="flex flex-col gap-4">
          {/* Metrics */}
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: "Hold Time",   value: holdLabel },
              { label: "Risk/Reward", value: rr ? `${rr.toFixed(2)}R` : "—" },
              { label: "Confidence",  value: trade.confidence_score
                  ? `${(trade.confidence_score * 100).toFixed(0)}%`
                  : "—"
              },
            ].map(({ label, value }) => (
              <div key={label} className="rounded-xl border border-white/[0.06] bg-surface p-3">
                <p className="mb-1 text-[10px] text-muted uppercase tracking-wider">{label}</p>
                <p className="font-space text-sm font-semibold text-primary">{value}</p>
              </div>
            ))}
          </div>

          {/* Confidence bar */}
          {trade.confidence_score != null && (
            <div>
              <div className="mb-1.5 flex justify-between text-[10px] text-muted">
                <span>Confidence Score</span>
                <span>{(trade.confidence_score * 100).toFixed(0)}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${(trade.confidence_score ?? 0) * 100}%` }}
                  transition={{ duration: 0.8, ease: "easeOut" }}
                  className="h-full rounded-full bg-purple"
                />
              </div>
            </div>
          )}

          {/* AI Reasoning */}
          {trade.ai_reasoning && (
            <div className="rounded-xl border border-purple/20 bg-purple/[0.06] p-4">
              <div className="mb-2 flex items-center gap-2">
                <Brain className="h-3.5 w-3.5 text-purple" />
                <span className="text-xs font-semibold text-purple uppercase tracking-wider">
                  AI Reasoning
                </span>
              </div>
              <p className="text-xs leading-relaxed text-muted">
                <TypewriterText text={trade.ai_reasoning} />
              </p>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

export default function TradesPage() {
  const [tab, setTab]       = useState<TradeStatus | "all">("all");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const { trades, loading } = useTrades({
    status: tab === "all" ? undefined : tab,
    limit: 100,
  });

  const filtered = trades.filter((t) =>
    t.symbol.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header */}
      <div className="border-b border-white/[0.06] px-6 py-4">
        <h1 className="font-space text-lg font-semibold text-primary">Trades</h1>
        <p className="text-xs text-muted">Full trade history with AI analysis</p>
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-4 border-b border-white/[0.06] px-6 py-3">
        <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-surface p-1">
          {TABS.map(({ label, value }) => (
            <button
              key={value}
              onClick={() => setTab(value)}
              className={cn(
                "rounded-lg px-4 py-1.5 text-xs font-semibold transition-all",
                tab === value
                  ? "bg-cyan/10 text-cyan"
                  : "text-muted hover:text-primary"
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="relative ml-auto">
          <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter symbol..."
            className="h-8 w-48 rounded-xl border border-white/[0.06] bg-surface pl-9 pr-3 text-xs text-primary placeholder-muted outline-none focus:border-cyan/40 focus:ring-1 focus:ring-cyan/20 transition-all"
          />
        </div>
      </div>

      {/* Table */}
      <div className="flex-1">
        <table className="w-full">
          <thead>
            <tr className="border-b border-white/[0.06]">
              {["Symbol", "Side", "Entry", "Exit", "Qty", "P&L", "Opened", "Status", ""].map((h) => (
                <th
                  key={h}
                  className="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-widest text-muted"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: 6 }).map((_, i) => <tr key={i}><td colSpan={9}><TradeRowSkeleton /></td></tr>)
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={9}>
                  <div className="flex flex-col items-center justify-center py-20 text-center">
                    <ArrowLeftRight className="mb-3 h-10 w-10 text-muted/20" />
                    <p className="text-sm text-muted">No trades found</p>
                  </div>
                </td>
              </tr>
            ) : (
              filtered.map((trade, i) => (
                <>
                  <motion.tr
                    key={trade.id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: i * 0.025 }}
                    onClick={() => setExpanded(expanded === trade.id ? null : trade.id)}
                    className={cn(
                      "cursor-pointer border-b border-white/[0.04] text-sm transition-colors",
                      expanded === trade.id
                        ? "bg-cyan/[0.03]"
                        : "hover:bg-white/[0.02]"
                    )}
                  >
                    <td className="px-4 py-3.5">
                      <span className="font-space font-bold text-primary">{trade.symbol}</span>
                    </td>
                    <td className="px-4 py-3.5">
                      <TradeBadge signal={trade.side === "buy" ? "BUY" : "SELL"} />
                    </td>
                    <td className="px-4 py-3.5 font-mono text-xs text-muted">
                      {formatCurrency(trade.entry_price ?? 0)}
                    </td>
                    <td className="px-4 py-3.5 font-mono text-xs text-muted">
                      {trade.exit_price ? formatCurrency(trade.exit_price) : "—"}
                    </td>
                    <td className="px-4 py-3.5 text-xs text-muted">{trade.qty}</td>
                    <td className={cn("px-4 py-3.5 font-space text-sm font-semibold", pnlColor(trade.pnl ?? 0))}>
                      {trade.pnl !== null ? formatCurrency(trade.pnl) : "—"}
                    </td>
                    <td className="px-4 py-3.5 text-xs text-muted">
                      <div className="flex items-center gap-1.5">
                        <Clock className="h-3 w-3" />
                        {new Date(trade.entry_at ?? trade.created_at).toLocaleDateString()}
                      </div>
                    </td>
                    <td className="px-4 py-3.5">
                      <span className={cn(
                        "rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
                        trade.status === "open"
                          ? "border-cyan/25 bg-cyan/10 text-cyan"
                          : "border-white/10 bg-white/5 text-muted"
                      )}>
                        {trade.status}
                      </span>
                    </td>
                    <td className="px-4 py-3.5">
                      <motion.div
                        animate={{ rotate: expanded === trade.id ? 180 : 0 }}
                        transition={{ duration: 0.2 }}
                      >
                        <ChevronDown className="h-4 w-4 text-muted" />
                      </motion.div>
                    </td>
                  </motion.tr>

                  <AnimatePresence>
                    {expanded === trade.id && (
                      <tr key={`${trade.id}-exp`}>
                        <td colSpan={9} className="p-0">
                          <ExpandedRow trade={trade} />
                        </td>
                      </tr>
                    )}
                  </AnimatePresence>
                </>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
