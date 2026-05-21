"use client";

import { useState, useCallback, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { BrainCircuit, Loader2, RefreshCw, Target, Zap } from "lucide-react";
import { TradeBadge } from "@/components/ui/TradeBadge";
import { SignalStrengthBar } from "@/components/ui/SignalStrengthBar";
import { SignalCardSkeleton } from "@/components/ui/LoadingSkeleton";
import { SignalDrawer } from "@/components/signals/SignalDrawer";
import { useRealtime, useRealtimeSignals } from "@/hooks/useRealtime";
import { createClient } from "@/lib/supabase/client";
import { cn, formatPercent, pnlColor } from "@/lib/utils";
import type { Signal, SignalOutcome } from "@/types";

const GLOW = {
  BUY:  "ring-1 ring-cyan/20 shadow-[0_0_24px_rgba(0,212,255,0.08)]",
  SELL: "ring-1 ring-danger/20 shadow-[0_0_24px_rgba(255,59,92,0.08)]",
  HOLD: "",
};

function SignalCard({ signal, index, onClick }: { signal: Signal; index: number; onClick: () => void }) {
  const sig = signal.signal.toUpperCase() as "BUY" | "SELL" | "HOLD";
  const outcome = signal.signal_outcomes?.[0];
  const confidence = signal.confidence ?? signal.strength;
  const trend = (signal.indicators?.trend as string | undefined) ?? "-";

  return (
    <motion.div
      initial={{ opacity: 0, y: -16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ delay: index * 0.04 }}
      layout
      onClick={onClick}
      className={cn("glass cursor-pointer rounded-2xl p-5 transition-transform hover:scale-[1.01]", GLOW[sig])}
    >
      {/* Top row */}
      <div className="mb-3 flex items-start justify-between">
        <div>
          <p className="font-space text-2xl font-bold text-primary">{signal.symbol}</p>
          <p className="mt-0.5 text-xs text-muted">{signal.strategy}</p>
        </div>
        <TradeBadge signal={sig} size="md" glow />
      </div>

      {/* Strength */}
      <SignalStrengthBar strength={signal.strength} signal={sig} />

      <div className="mt-4 grid grid-cols-3 gap-2">
        <div className="rounded-lg border border-white/[0.06] bg-base/60 px-2.5 py-2">
          <p className="text-[9px] font-semibold uppercase text-muted">Conf</p>
          <p className="mt-0.5 font-space text-xs font-semibold text-primary">
            {(confidence * 100).toFixed(0)}%
          </p>
        </div>
        <div className="rounded-lg border border-white/[0.06] bg-base/60 px-2.5 py-2">
          <p className="text-[9px] font-semibold uppercase text-muted">Criteria</p>
          <p className="mt-0.5 font-space text-xs font-semibold text-primary">
            {signal.criteria_met ?? "-"}/7
          </p>
        </div>
        <div className="rounded-lg border border-white/[0.06] bg-base/60 px-2.5 py-2">
          <p className="text-[9px] font-semibold uppercase text-muted">Trend</p>
          <p className="mt-0.5 truncate font-space text-xs font-semibold text-primary">
            {trend}
          </p>
        </div>
      </div>

      {outcome ? (
        <div className="mt-4 rounded-lg border border-white/[0.06] bg-base/60 p-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <Target className="h-3 w-3 text-cyan" />
              <p className="text-[10px] font-semibold uppercase text-muted">Outcome</p>
            </div>
            <p className={cn("font-space text-xs font-bold", pnlColor(outcome.outcome_score ?? 0))}>
              {(outcome.outcome_score ?? 0).toFixed(2)}
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {[
              ["1h", outcome.return_1h_pct],
              ["1d", outcome.return_1d_pct],
              ["3d", outcome.return_3d_pct],
            ].map(([label, value]) => (
              <div key={label} className="text-center">
                <p className="text-[9px] uppercase text-muted">{label}</p>
                <p className={cn("font-space text-xs font-semibold", pnlColor(Number(value ?? 0)))}>
                  {typeof value === "number" ? formatPercent(value, 2) : "-"}
                </p>
              </div>
            ))}
          </div>
          <div className="mt-2 flex items-center justify-between text-[10px] text-muted">
            <span>Best {formatPercent(outcome.max_favorable_pct ?? 0, 2)}</span>
            <span>Worst {formatPercent(outcome.max_adverse_pct ?? 0, 2)}</span>
          </div>
        </div>
      ) : (
        <div className="mt-4 rounded-lg border border-white/[0.06] bg-base/40 px-3 py-2 text-[10px] text-muted">
          Awaiting enough future bars for grading
        </div>
      )}

      {/* Timestamp */}
      <p className="mt-3 text-[10px] text-muted">
        {new Date(signal.created_at).toLocaleString([], {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })}
      </p>
    </motion.div>
  );
}

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [evaluating, setEvaluating] = useState(false);
  const [evalMessage, setEvalMessage] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activeSignal, setActiveSignal] = useState<Signal | null>(null);
  const supabase = createClient();

  useEffect(() => {
    async function loadSignals() {
      try {
        const { data, error } = await supabase
          .from("signals")
          .select("*, signal_outcomes(*)")
          .order("created_at", { ascending: false })
          .limit(30);

        if (error) {
          setLoadError("Signal outcome tables are not available yet. Apply the research migration.");
          setSignals([]);
          setLoading(false);
          return;
        }
        setSignals((data as Signal[]) ?? []);
        setLoading(false);
      } catch {
        setLoadError("Could not load signals.");
        setSignals([]);
        setLoading(false);
      }
    }

    loadSignals();
  }, []);

  const handleNew = useCallback((payload: Record<string, unknown>) => {
    setSignals((prev) => [payload.new as Signal, ...prev].slice(0, 50));
  }, []);

  const handleOutcome = useCallback((payload: Record<string, unknown>) => {
    const outcome = payload.new as SignalOutcome;
    setSignals((prev) =>
      prev.map((signal) =>
        signal.id === outcome.signal_id
          ? { ...signal, signal_outcomes: [outcome] }
          : signal
      )
    );
  }, []);

  useRealtimeSignals(handleNew);
  useRealtime({ table: "signal_outcomes", event: "*", onData: handleOutcome });

  const gradedSignals = signals.filter((signal) => signal.signal_outcomes?.length).length;
  const avgOutcome = signals.reduce((sum, signal) => {
    return sum + (signal.signal_outcomes?.[0]?.outcome_score ?? 0);
  }, 0) / Math.max(gradedSignals, 1);

  async function evaluateOutcomes() {
    setEvaluating(true);
    setEvalMessage(null);
    try {
      const res = await fetch("/api/signals/evaluate?limit=50", { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        const message =
          data.error === "Not Found"
            ? "Restart the bot API so it loads the new outcome evaluator."
            : data.error ?? "Evaluation failed";
        throw new Error(message);
      }
      setEvalMessage(`Graded ${data.evaluated} signals, skipped ${data.skipped}`);
    } catch (e) {
      setEvalMessage(e instanceof Error ? e.message : "Evaluation failed");
    } finally {
      setEvaluating(false);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <SignalDrawer signal={activeSignal} onClose={() => setActiveSignal(null)} />
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-4">
        <div>
          <h1 className="font-space text-lg font-semibold text-primary">Signals</h1>
          <p className="text-xs text-muted">AI-generated trade signals with live outcome grading</p>
        </div>
        <div className="flex items-center gap-2">
          {evalMessage && <span className="hidden text-xs text-muted md:inline">{evalMessage}</span>}
          <button
            onClick={evaluateOutcomes}
            disabled={evaluating}
            className="flex items-center gap-2 rounded-lg border border-cyan/20 bg-cyan/[0.06] px-3 py-1.5 text-xs font-medium text-cyan transition-opacity disabled:cursor-not-allowed disabled:opacity-60"
          >
            {evaluating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            Evaluate
          </button>
        </div>
      </div>

      <div className="flex-1 p-6">
        <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="rounded-lg border border-white/[0.06] bg-surface px-4 py-3">
            <div className="mb-1 flex items-center gap-2 text-[10px] font-semibold uppercase text-muted">
              <Zap className="h-3 w-3 text-cyan" />
              Signals
            </div>
            <p className="font-space text-xl font-bold text-primary">{signals.length}</p>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-surface px-4 py-3">
            <div className="mb-1 flex items-center gap-2 text-[10px] font-semibold uppercase text-muted">
              <Target className="h-3 w-3 text-success" />
              Graded
            </div>
            <p className="font-space text-xl font-bold text-primary">{gradedSignals}</p>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-surface px-4 py-3">
            <div className="mb-1 flex items-center gap-2 text-[10px] font-semibold uppercase text-muted">
              <BrainCircuit className="h-3 w-3 text-purple" />
              Avg Outcome
            </div>
            <p className={cn("font-space text-xl font-bold", pnlColor(avgOutcome))}>
              {avgOutcome.toFixed(2)}
            </p>
          </div>
        </div>

        {loading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <SignalCardSkeleton key={i} />
            ))}
          </div>
        ) : signals.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-32 text-center">
            <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-white/[0.06] bg-surface">
              <Zap className="h-7 w-7 text-muted/40" />
            </div>
            <p className="text-sm font-medium text-muted">
              {loadError ?? "No signals yet"}
            </p>
            <p className="mt-1 text-xs text-muted/60">
              {loadError ? "Run the research migration, then refresh this page" : "Start the bot to begin generating signals"}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            <AnimatePresence>
              {signals.map((sig, i) => (
                <SignalCard key={sig.id} signal={sig} index={i} onClick={() => setActiveSignal(sig)} />
              ))}
            </AnimatePresence>
          </div>
        )}
      </div>
    </div>
  );
}
