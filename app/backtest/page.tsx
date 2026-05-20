"use client";

import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  Activity,
  BarChart3,
  Beaker,
  BrainCircuit,
  Gauge,
  Loader2,
  Play,
  ShieldAlert,
  Target,
  TrendingUp,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn, formatCurrency, formatNumber, formatPercent, pnlColor } from "@/lib/utils";
import type { BacktestRun, BacktestTrade } from "@/types";

const DEFAULT_SYMBOLS = "NVDA, TSLA, AMD, META, AMZN, MSFT, AAPL, PLTR";

function MetricTile({
  label,
  value,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: string;
  icon: typeof TrendingUp;
  tone?: "neutral" | "good" | "bad" | "cyan";
}) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-surface px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase text-muted">{label}</p>
        <Icon
          className={cn(
            "h-3.5 w-3.5",
            tone === "good" && "text-success",
            tone === "bad" && "text-danger",
            tone === "cyan" && "text-cyan",
            tone === "neutral" && "text-muted"
          )}
        />
      </div>
      <p className="font-space text-lg font-bold text-primary">{value}</p>
    </div>
  );
}

function RunRow({
  run,
  active,
  onSelect,
}: {
  run: BacktestRun;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className={cn(
        "w-full border-b border-white/[0.04] px-5 py-4 text-left transition-colors hover:bg-white/[0.03]",
        active && "bg-cyan/[0.05]"
      )}
    >
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="font-space text-sm font-semibold text-primary">{run.strategy}</p>
          <p className="mt-1 text-xs text-muted">
            {run.symbols.slice(0, 5).join(", ")}
            {run.symbols.length > 5 ? ` +${run.symbols.length - 5}` : ""} - {run.timeframe}
          </p>
        </div>
        <div className="text-right">
          <p className={cn("font-space text-sm font-bold", pnlColor(run.total_return_pct))}>
            {formatPercent(run.total_return_pct, 2)}
          </p>
          <p className="mt-1 text-[10px] text-muted">{run.total_trades} trades</p>
        </div>
      </div>
    </button>
  );
}

export default function BacktestPage() {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [trades, setTrades] = useState<BacktestTrade[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  const [days, setDays] = useState(180);
  const [timeframe, setTimeframe] = useState("1Hour");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const supabase = createClient();

  useEffect(() => {
    async function loadRuns() {
      try {
        const { data, error } = await supabase
          .from("backtest_runs")
          .select("*")
          .order("created_at", { ascending: false })
          .limit(12);

        if (error) {
          setLoadError("Research tables are not available yet. Apply the research migration.");
          setLoading(false);
          return;
        }
        const next = (data as BacktestRun[]) ?? [];
        setRuns(next);
        setSelectedRunId(next[0]?.id ?? null);
        setLoading(false);
      } catch {
        setLoadError("Could not load research runs.");
        setLoading(false);
      }
    }

    loadRuns();
  }, []);

  useEffect(() => {
    if (!selectedRunId) {
      setTrades([]);
      return;
    }

    async function loadTrades() {
      try {
        const { data } = await supabase
          .from("backtest_trades")
          .select("*")
          .eq("run_id", selectedRunId)
          .order("entry_at", { ascending: false })
          .limit(50);
        setTrades((data as BacktestTrade[]) ?? []);
      } catch {
        setTrades([]);
      }
    }

    loadTrades();
  }, [selectedRunId]);

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId]
  );

  async function runBacktest() {
    setRunning(true);
    setError(null);
    try {
      const res = await fetch("/api/backtest", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          symbols,
          days,
          timeframe,
          initial_equity: 100000,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        const message =
          data.error === "Not Found"
            ? "Restart the bot API so it loads the new backtest endpoint."
            : data.error ?? "Backtest failed";
        throw new Error(message);
      }
      const next = data as BacktestRun;
      setRuns((prev) => [next, ...prev].slice(0, 12));
      setSelectedRunId(next.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Backtest failed");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-4">
        <div>
          <h1 className="font-space text-lg font-semibold text-primary">Research Lab</h1>
          <p className="text-xs text-muted">Backtest every idea before the bot risks capital</p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-cyan/20 bg-cyan/[0.06] px-3 py-1.5">
          <BrainCircuit className="h-3.5 w-3.5 text-cyan" />
          <span className="text-xs font-medium text-cyan">Proof Engine</span>
        </div>
      </div>

      <div className="flex-1 space-y-6 p-6">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[360px_1fr]">
          <div className="glass rounded-2xl">
            <div className="border-b border-white/[0.06] px-5 py-4">
              <div className="flex items-center gap-2">
                <Beaker className="h-4 w-4 text-cyan" />
                <h2 className="font-space text-sm font-semibold text-primary">Experiment</h2>
              </div>
            </div>
            <div className="space-y-4 p-5">
              <label className="block">
                <span className="text-[10px] font-semibold uppercase text-muted">Symbols</span>
                <textarea
                  value={symbols}
                  onChange={(e) => setSymbols(e.target.value)}
                  className="mt-2 min-h-24 w-full resize-none rounded-lg border border-white/[0.06] bg-base px-3 py-2 text-sm text-primary outline-none transition-colors focus:border-cyan/40"
                />
              </label>

              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-[10px] font-semibold uppercase text-muted">Lookback</span>
                  <input
                    type="number"
                    min={30}
                    max={730}
                    value={days}
                    onChange={(e) => setDays(Number(e.target.value))}
                    className="mt-2 w-full rounded-lg border border-white/[0.06] bg-base px-3 py-2 text-sm text-primary outline-none focus:border-cyan/40"
                  />
                </label>
                <label className="block">
                  <span className="text-[10px] font-semibold uppercase text-muted">Timeframe</span>
                  <select
                    value={timeframe}
                    onChange={(e) => setTimeframe(e.target.value)}
                    className="mt-2 w-full rounded-lg border border-white/[0.06] bg-base px-3 py-2 text-sm text-primary outline-none focus:border-cyan/40"
                  >
                    <option value="15Min">15Min</option>
                    <option value="1Hour">1Hour</option>
                    <option value="1Day">1Day</option>
                  </select>
                </label>
              </div>

              <button
                onClick={runBacktest}
                disabled={running}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-cyan px-4 py-2.5 text-sm font-bold text-base transition-opacity disabled:cursor-not-allowed disabled:opacity-60"
              >
                {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                Run Backtest
              </button>

              {error && (
                <div className="rounded-lg border border-danger/20 bg-danger/[0.06] px-3 py-2 text-xs text-danger">
                  {error}
                </div>
              )}
            </div>
          </div>

          <div className="space-y-4">
            {selectedRun ? (
              <>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <MetricTile
                    label="Return"
                    value={formatPercent(selectedRun.total_return_pct, 2)}
                    icon={TrendingUp}
                    tone={selectedRun.total_return_pct >= 0 ? "good" : "bad"}
                  />
                  <MetricTile
                    label="Max Drawdown"
                    value={formatPercent(-selectedRun.max_drawdown_pct, 2)}
                    icon={ShieldAlert}
                    tone={selectedRun.max_drawdown_pct <= 8 ? "good" : "bad"}
                  />
                  <MetricTile
                    label="Win Rate"
                    value={formatPercent(selectedRun.win_rate, 1)}
                    icon={Target}
                    tone="cyan"
                  />
                  <MetricTile
                    label="Profit Factor"
                    value={formatNumber(selectedRun.profit_factor, 2)}
                    icon={Gauge}
                    tone={selectedRun.profit_factor >= 1.3 ? "good" : "neutral"}
                  />
                </div>

                <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                  <div className="glass rounded-2xl p-5 lg:col-span-2">
                    <div className="mb-4 flex items-center justify-between">
                      <div>
                        <h2 className="font-space text-sm font-semibold text-primary">Run Summary</h2>
                        <p className="text-xs text-muted">
                          {new Date(selectedRun.start_at).toLocaleDateString()} to{" "}
                          {new Date(selectedRun.end_at).toLocaleDateString()}
                        </p>
                      </div>
                      <span className="rounded-lg border border-white/[0.06] bg-base px-3 py-1 text-xs text-muted">
                        {selectedRun.total_trades} trades
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                      <MetricTile
                        label="Final Equity"
                        value={formatCurrency(selectedRun.final_equity)}
                        icon={Activity}
                      />
                      <MetricTile
                        label="Expectancy"
                        value={`${selectedRun.expectancy_r.toFixed(2)}R`}
                        icon={BarChart3}
                        tone={selectedRun.expectancy_r > 0 ? "good" : "bad"}
                      />
                      <MetricTile
                        label="Sharpe"
                        value={selectedRun.sharpe.toFixed(2)}
                        icon={Gauge}
                      />
                      <MetricTile
                        label="Wins / Losses"
                        value={`${selectedRun.winning_trades}/${selectedRun.losing_trades}`}
                        icon={Target}
                      />
                    </div>
                  </div>

                  <div className="glass rounded-2xl">
                    <div className="border-b border-white/[0.06] px-5 py-4">
                      <h2 className="font-space text-sm font-semibold text-primary">Recent Runs</h2>
                    </div>
                    <div>
                      {runs.map((run) => (
                        <RunRow
                          key={run.id}
                          run={run}
                          active={run.id === selectedRunId}
                          onSelect={() => setSelectedRunId(run.id)}
                        />
                      ))}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div className="glass flex min-h-64 items-center justify-center rounded-2xl">
                <div className="px-6 text-center">
                  <p className="text-sm font-medium text-muted">
                    {loading ? "Loading runs..." : loadError ?? "No backtests yet"}
                  </p>
                  {!loading && loadError && (
                    <p className="mt-2 text-xs text-muted/70">
                      Run `supabase/migrations/002_research_lab.sql` before using this lab.
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="glass rounded-2xl">
          <div className="border-b border-white/[0.06] px-5 py-4">
            <h2 className="font-space text-sm font-semibold text-primary">Trade Forensics</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left">
              <thead>
                <tr className="border-b border-white/[0.06] text-[10px] uppercase text-muted">
                  <th className="px-5 py-3 font-semibold">Symbol</th>
                  <th className="px-5 py-3 font-semibold">Side</th>
                  <th className="px-5 py-3 font-semibold">Entry</th>
                  <th className="px-5 py-3 font-semibold">Exit</th>
                  <th className="px-5 py-3 font-semibold">P&L</th>
                  <th className="px-5 py-3 font-semibold">R</th>
                  <th className="px-5 py-3 font-semibold">Reason</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade, index) => (
                  <motion.tr
                    key={trade.id}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: index * 0.02 }}
                    className="border-b border-white/[0.04] text-sm"
                  >
                    <td className="px-5 py-3 font-space font-semibold text-primary">{trade.symbol}</td>
                    <td className="px-5 py-3">
                      <span
                        className={cn(
                          "rounded-md px-2 py-1 text-[10px] font-bold uppercase",
                          trade.side === "buy"
                            ? "bg-success/10 text-success"
                            : "bg-danger/10 text-danger"
                        )}
                      >
                        {trade.side}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-muted">{formatCurrency(trade.entry_price)}</td>
                    <td className="px-5 py-3 text-muted">{formatCurrency(trade.exit_price)}</td>
                    <td className={cn("px-5 py-3 font-space font-semibold", pnlColor(trade.pnl))}>
                      {formatCurrency(trade.pnl)}
                    </td>
                    <td className={cn("px-5 py-3 font-space font-semibold", pnlColor(trade.r_multiple))}>
                      {trade.r_multiple.toFixed(2)}R
                    </td>
                    <td className="px-5 py-3 text-xs text-muted">{trade.exit_reason}</td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
