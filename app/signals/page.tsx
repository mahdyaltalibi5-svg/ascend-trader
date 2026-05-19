"use client";

import { useState, useCallback, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Zap } from "lucide-react";
import { TradeBadge } from "@/components/ui/TradeBadge";
import { SignalStrengthBar } from "@/components/ui/SignalStrengthBar";
import { SignalCardSkeleton } from "@/components/ui/LoadingSkeleton";
import { useRealtimeSignals } from "@/hooks/useRealtime";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import type { Signal } from "@/types";

const GLOW = {
  BUY:  "ring-1 ring-cyan/20 shadow-[0_0_24px_rgba(0,212,255,0.08)]",
  SELL: "ring-1 ring-danger/20 shadow-[0_0_24px_rgba(255,59,92,0.08)]",
  HOLD: "",
};

function SignalCard({ signal, index }: { signal: Signal; index: number }) {
  const sig = signal.signal.toUpperCase() as "BUY" | "SELL" | "HOLD";
  const ind = (signal.indicators ?? {}) as Record<string, number>;

  return (
    <motion.div
      initial={{ opacity: 0, y: -16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ delay: index * 0.04 }}
      layout
      className={cn("glass rounded-2xl p-5", GLOW[sig])}
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

      {/* Indicators */}
      {Object.keys(ind).length > 0 && (
        <div className="mt-4 grid grid-cols-3 gap-2">
          {Object.entries(ind)
            .slice(0, 3)
            .map(([key, val]) => (
              <div
                key={key}
                className="rounded-lg border border-white/[0.06] bg-base/60 px-2.5 py-2"
              >
                <p className="text-[9px] font-semibold uppercase tracking-wider text-muted">
                  {key}
                </p>
                <p className="mt-0.5 font-space text-xs font-semibold text-primary">
                  {typeof val === "number" ? val.toFixed(2) : val}
                </p>
              </div>
            ))}
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
  const supabase = createClient();

  useEffect(() => {
    supabase
      .from("signals")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(30)
      .then(({ data }) => {
        setSignals((data as Signal[]) ?? []);
        setLoading(false);
      });
  }, []);

  const handleNew = useCallback((payload: Record<string, unknown>) => {
    setSignals((prev) => [payload.new as Signal, ...prev].slice(0, 50));
  }, []);

  useRealtimeSignals(handleNew);

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-4">
        <div>
          <h1 className="font-space text-lg font-semibold text-primary">Signals</h1>
          <p className="text-xs text-muted">AI-generated trade signals — live via Supabase Realtime</p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-cyan/20 bg-cyan/[0.06] px-3 py-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-cyan animate-pulse-slow" />
          <span className="text-xs font-medium text-cyan">Live Feed</span>
        </div>
      </div>

      <div className="flex-1 p-6">
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
            <p className="text-sm font-medium text-muted">No signals yet</p>
            <p className="mt-1 text-xs text-muted/60">
              Start the bot to begin generating signals
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            <AnimatePresence>
              {signals.map((sig, i) => (
                <SignalCard key={sig.id} signal={sig} index={i} />
              ))}
            </AnimatePresence>
          </div>
        )}
      </div>
    </div>
  );
}
