"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Bot, ShieldAlert, Plus, X, Power, AlertCircle } from "lucide-react";
import { BotStatusBadge } from "@/components/ui/BotStatusBadge";
import { useRealtimeTrades } from "@/hooks/useRealtime";
import { createClient } from "@/lib/supabase/client";
import { useBotStatus } from "@/hooks/useBotStatus";
import { cn } from "@/lib/utils";
import { SUPPORTED_SYMBOLS, STRATEGIES, TIMEFRAMES } from "@/lib/constants";
import type { BotLog } from "@/types";

const LOG_COLOR = {
  info:    "text-cyan",
  warning: "text-amber-400",
  error:   "text-danger",
};

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: React.ElementType;
  children: React.ReactNode;
}) {
  return (
    <div className="glass rounded-2xl">
      <div className="flex items-center gap-3 border-b border-white/[0.06] px-5 py-4">
        <Icon className="h-4 w-4 text-muted" />
        <h2 className="font-space text-sm font-semibold text-primary">{title}</h2>
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

function RangeSlider({
  label,
  value,
  min,
  max,
  step = 1,
  unit = "%",
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange: (v: number) => void;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs text-muted">{label}</span>
        <span className="font-space text-xs font-semibold text-cyan">
          {value}{unit}
        </span>
      </div>
      <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className="h-full rounded-full bg-gradient-to-r from-cyan/70 to-cyan transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        style={{ position: "relative", marginTop: -6 }}
      />
    </div>
  );
}

export default function SettingsPage() {
  const { status } = useBotStatus();
  const [toggling, setToggling] = useState(false);
  const [interval, setIntervalVal] = useState("15Min");
  const [watchlist, setWatchlist] = useState<string[]>(["AAPL", "NVDA", "TSLA", "SPY"]);
  const [newSymbol, setNewSymbol] = useState("");
  const [maxPositions, setMaxPositions] = useState(5);
  const [posSize, setPosSize] = useState(10);
  const [dailyLimit, setDailyLimit] = useState(5);
  const [logs, setLogs] = useState<BotLog[]>([]);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const supabase = createClient();

  useEffect(() => {
    supabase
      .from("bot_logs")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(100)
      .then(({ data }) => setLogs(((data as BotLog[]) ?? []).reverse()));
  }, []);

  // Realtime log subscription
  const handleNewLog = useCallback((payload: Record<string, unknown>) => {
    setLogs((prev) => [...prev, payload.new as BotLog].slice(-200));
    setTimeout(() => logsEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }, []);

  useEffect(() => {
    const channel = supabase
      .channel("bot_logs_rt")
      .on("postgres_changes" as never, { event: "INSERT", schema: "public", table: "bot_logs" }, handleNewLog)
      .subscribe();
    return () => { supabase.removeChannel(channel); };
  }, [handleNewLog, supabase]);

  const toggleBot = async () => {
    setToggling(true);
    try {
      await fetch("/api/bot/toggle", { method: "POST" });
    } finally {
      setToggling(false);
    }
  };

  const addSymbol = () => {
    const sym = newSymbol.trim().toUpperCase();
    if (sym && !watchlist.includes(sym)) {
      setWatchlist((prev) => [...prev, sym]);
    }
    setNewSymbol("");
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="border-b border-white/[0.06] px-6 py-4">
        <h1 className="font-space text-lg font-semibold text-primary">Settings</h1>
        <p className="text-xs text-muted">Bot configuration, risk parameters, and logs</p>
      </div>

      <div className="flex-1 space-y-5 p-6">
        {/* Bot Controls */}
        <Section title="Bot Controls" icon={Bot}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <BotStatusBadge status={status} />
              <span className="text-xs text-muted">
                {status === "running" ? "Bot is actively scanning markets" : "Bot is offline"}
              </span>
            </div>
            <button
              onClick={toggleBot}
              disabled={toggling}
              className={cn(
                "flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-semibold transition-all",
                status === "running"
                  ? "bg-danger/10 text-danger ring-1 ring-danger/25 hover:bg-danger/20"
                  : "bg-success/10 text-success ring-1 ring-success/25 hover:bg-success/20"
              )}
            >
              <Power className="h-3.5 w-3.5" />
              {toggling ? "Updating..." : status === "running" ? "Stop Bot" : "Start Bot"}
            </button>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-4">
            <div>
              <p className="mb-2 text-xs text-muted">Scan Interval</p>
              <div className="flex gap-1.5 flex-wrap">
                {TIMEFRAMES.map(({ value, label }) => (
                  <button
                    key={value}
                    onClick={() => setIntervalVal(value)}
                    className={cn(
                      "rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors",
                      interval === value
                        ? "bg-cyan/10 text-cyan ring-1 ring-cyan/20"
                        : "bg-surface text-muted hover:text-primary"
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <p className="mb-2 text-xs text-muted">Strategy</p>
              <select className="w-full rounded-xl border border-white/[0.06] bg-surface px-3 py-2 text-xs text-primary outline-none focus:border-cyan/40">
                {STRATEGIES.map(({ id, label }) => (
                  <option key={id} value={id}>{label}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Watchlist */}
          <div className="mt-5">
            <p className="mb-2.5 text-xs text-muted">Watchlist</p>
            <div className="flex flex-wrap gap-2">
              {watchlist.map((sym) => (
                <span
                  key={sym}
                  className="flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-surface px-2.5 py-1 text-xs font-medium text-primary"
                >
                  {sym}
                  <button
                    onClick={() => setWatchlist((prev) => prev.filter((s) => s !== sym))}
                    className="text-muted hover:text-danger transition-colors"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
              <div className="flex items-center gap-1.5">
                <input
                  value={newSymbol}
                  onChange={(e) => setNewSymbol(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addSymbol()}
                  placeholder="Add symbol"
                  className="h-7 w-24 rounded-lg border border-white/[0.06] bg-surface px-2 text-xs text-primary placeholder-muted outline-none focus:border-cyan/40"
                />
                <button
                  onClick={addSymbol}
                  className="flex h-7 w-7 items-center justify-center rounded-lg bg-cyan/10 text-cyan hover:bg-cyan/20 transition-colors"
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </div>
        </Section>

        {/* Risk Parameters */}
        <Section title="Risk Parameters" icon={ShieldAlert}>
          <div className="space-y-5">
            <RangeSlider
              label="Max Open Positions"
              value={maxPositions}
              min={1}
              max={20}
              unit=""
              onChange={setMaxPositions}
            />
            <RangeSlider
              label="Position Size (% of portfolio)"
              value={posSize}
              min={1}
              max={50}
              onChange={setPosSize}
            />
            <RangeSlider
              label="Daily Loss Limit"
              value={dailyLimit}
              min={1}
              max={25}
              onChange={setDailyLimit}
            />
          </div>
          <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-400/15 bg-amber-400/[0.06] p-3">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-amber-400" />
            <p className="text-[10px] leading-relaxed text-amber-400/80">
              Risk parameters apply to new trades only. Changes take effect on the next bot cycle.
            </p>
          </div>
        </Section>

        {/* Bot Logs */}
        <Section title="Bot Logs" icon={AlertCircle}>
          <div className="h-60 overflow-y-auto rounded-xl border border-white/[0.06] bg-base p-3 font-mono text-[11px]">
            {logs.length === 0 ? (
              <p className="py-6 text-center text-muted">No logs yet</p>
            ) : (
              <div className="space-y-1">
                <AnimatePresence>
                  {logs.map((log) => (
                    <motion.div
                      key={log.id}
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      className="flex gap-2"
                    >
                      <span className="text-muted/50 flex-shrink-0">
                        {new Date(log.created_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                      </span>
                      <span className={cn("uppercase flex-shrink-0 font-bold", LOG_COLOR[log.level as keyof typeof LOG_COLOR])}>
                        [{log.level}]
                      </span>
                      <span className="text-muted">{log.message}</span>
                    </motion.div>
                  ))}
                </AnimatePresence>
                <div ref={logsEndRef} />
              </div>
            )}
          </div>
        </Section>
      </div>
    </div>
  );
}
