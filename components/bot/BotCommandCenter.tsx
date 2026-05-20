"use client";

import { useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  BrainCircuit,
  Clock3,
  Play,
  Power,
  Radar,
  RefreshCw,
  ShieldCheck,
  Signal,
} from "lucide-react";
import { BotStatusBadge } from "@/components/ui/BotStatusBadge";
import { useBotStatus } from "@/hooks/useBotStatus";
import { cn } from "@/lib/utils";

function timeAgo(value?: string | null) {
  if (!value) return "Never";
  const ms = Date.now() - new Date(value).getTime();
  if (ms < 60_000) return "Just now";
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  return `${Math.floor(ms / 86_400_000)}d ago`;
}

function MiniMetric({
  label,
  value,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: string;
  icon: typeof Activity;
  tone?: "neutral" | "good" | "danger" | "cyan";
}) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base/70 px-3 py-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[9px] font-semibold uppercase text-muted">{label}</span>
        <Icon
          className={cn(
            "h-3 w-3",
            tone === "good" && "text-success",
            tone === "danger" && "text-danger",
            tone === "cyan" && "text-cyan",
            tone === "neutral" && "text-muted"
          )}
        />
      </div>
      <p className="font-space text-sm font-bold text-primary">{value}</p>
    </div>
  );
}

export function BotCommandCenter({ compact = false }: { compact?: boolean }) {
  const { status, bot, loading } = useBotStatus();
  const [busy, setBusy] = useState<"toggle" | "scan" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function toggleBot() {
    setBusy("toggle");
    setMessage(null);
    try {
      const res = await fetch("/api/bot/toggle", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Bot toggle failed");
      setMessage(data.running ? "Bot start requested" : "Bot stop requested");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Bot toggle failed");
    } finally {
      setBusy(null);
    }
  }

  async function triggerScan() {
    setBusy("scan");
    setMessage(null);
    try {
      const res = await fetch("/api/bot/scan", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Scan failed");
      setMessage(data.status === "scan_triggered" ? "Manual scan triggered" : "Scan request sent");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Scan failed");
    } finally {
      setBusy(null);
    }
  }

  const connected = status !== "error" && Boolean(bot);
  const running = status === "running";
  const blocked = !connected || bot?.circuit_breaker_active;

  return (
    <div className="glass rounded-2xl">
      <div className="flex flex-col gap-4 border-b border-white/[0.06] px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-cyan/15 bg-cyan/[0.06]">
            <Bot className="h-5 w-5 text-cyan" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-space text-sm font-semibold text-primary">Bot Command Center</h2>
              <BotStatusBadge status={status} />
            </div>
            <p className="mt-1 text-xs text-muted">
              {loading
                ? "Checking bot connection..."
                : connected
                  ? running
                    ? "Scanning markets, monitoring positions, and grading signals"
                    : "Connected, standing by for paper trading"
                  : "Bot API is not connected yet"}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {message && <span className="text-xs text-muted">{message}</span>}
          <button
            onClick={triggerScan}
            disabled={!running || busy !== null}
            className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-surface px-3 py-2 text-xs font-semibold text-primary transition-colors hover:border-cyan/30 hover:text-cyan disabled:cursor-not-allowed disabled:opacity-45"
          >
            {busy === "scan" ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Radar className="h-3.5 w-3.5" />}
            Scan Now
          </button>
          <button
            onClick={toggleBot}
            disabled={busy !== null || !connected}
            className={cn(
              "flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-bold transition-colors disabled:cursor-not-allowed disabled:opacity-45",
              running
                ? "bg-danger/10 text-danger ring-1 ring-danger/25 hover:bg-danger/20"
                : "bg-success/10 text-success ring-1 ring-success/25 hover:bg-success/20"
            )}
          >
            {busy === "toggle" ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : running ? <Power className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            {running ? "Stop" : "Start"}
          </button>
        </div>
      </div>

      <div className={cn("grid gap-3 p-5", compact ? "grid-cols-2 lg:grid-cols-4" : "grid-cols-2 xl:grid-cols-4")}>
        <MiniMetric label="Last scan" value={timeAgo(bot?.last_scan_at)} icon={Clock3} tone="cyan" />
        <MiniMetric label="Signals today" value={String(bot?.signals_today ?? 0)} icon={Signal} tone="cyan" />
        <MiniMetric label="Trades today" value={String(bot?.trades_today ?? 0)} icon={Activity} tone="good" />
        <MiniMetric
          label="Risk state"
          value={bot?.circuit_breaker_active ? "Halted" : blocked ? "Blocked" : "Clear"}
          icon={bot?.circuit_breaker_active ? AlertTriangle : ShieldCheck}
          tone={bot?.circuit_breaker_active || blocked ? "danger" : "good"}
        />
      </div>

      {!compact && (
        <div className="grid gap-3 border-t border-white/[0.06] px-5 py-4 md:grid-cols-3">
          <div className="flex items-start gap-2 rounded-lg bg-base/50 p-3">
            <BrainCircuit className="mt-0.5 h-3.5 w-3.5 text-purple" />
            <div>
              <p className="text-[10px] font-semibold uppercase text-muted">Strategy</p>
              <p className="mt-1 text-xs text-primary">{bot?.strategy ?? "ascend_elite_v3"}</p>
            </div>
          </div>
          <div className="flex items-start gap-2 rounded-lg bg-base/50 p-3">
            <Radar className="mt-0.5 h-3.5 w-3.5 text-cyan" />
            <div>
              <p className="text-[10px] font-semibold uppercase text-muted">Last signal</p>
              <p className="mt-1 text-xs text-primary">{timeAgo(bot?.last_signal_at)}</p>
            </div>
          </div>
          <div className="flex items-start gap-2 rounded-lg bg-base/50 p-3">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 text-success" />
            <div>
              <p className="text-[10px] font-semibold uppercase text-muted">Outcome grading</p>
              <p className="mt-1 text-xs text-primary">{timeAgo(bot?.last_outcome_eval_at)}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
