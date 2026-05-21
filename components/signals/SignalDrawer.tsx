"use client";

import { useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  TrendingUp,
  TrendingDown,
  Shield,
  ShieldAlert,
  BrainCircuit,
  Activity,
  Flame,
  Wallet,
  BarChart3,
  Target,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Layers,
  Route,
  Zap,
  ChevronRight,
} from "lucide-react";
import { cn, formatPercent, pnlColor } from "@/lib/utils";
import { TradeBadge } from "@/components/ui/TradeBadge";
import type { Signal } from "@/types";

// ─── Helpers ────────────────────────────────────────────────────────────────

function pct(v: unknown): string {
  const n = Number(v ?? 0);
  if (!isFinite(n) || n === 0) return "–";
  return n > 0 ? `+${(n * 100).toFixed(1)}%` : `${(n * 100).toFixed(1)}%`;
}

function score(v: unknown): string {
  const n = Number(v ?? 0);
  if (!isFinite(n)) return "–";
  return n.toFixed(2);
}

function conf(v: unknown): string {
  const n = Number(v ?? 0);
  if (!isFinite(n)) return "–";
  return `${Math.round(n * 100)}%`;
}

function price(v: unknown): string {
  const n = Number(v ?? 0);
  if (!isFinite(n) || n === 0) return "–";
  return `$${n.toFixed(2)}`;
}

function boostColor(v: unknown) {
  const n = Number(v ?? 0);
  if (n > 0.005) return "text-success";
  if (n < -0.005) return "text-danger";
  return "text-muted";
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function DrawerSection({
  title,
  icon: Icon,
  iconColor = "text-cyan",
  children,
}: {
  title: string;
  icon: React.ElementType;
  iconColor?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border-t border-white/[0.06] pt-5">
      <div className="mb-3 flex items-center gap-2">
        <Icon className={cn("h-3.5 w-3.5", iconColor)} />
        <p className="text-[10px] font-bold uppercase tracking-widest text-muted">{title}</p>
      </div>
      {children}
    </div>
  );
}

function MiniStat({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base/60 px-3 py-2">
      <p className="text-[9px] font-semibold uppercase text-muted">{label}</p>
      <p className={cn("mt-0.5 font-space text-xs font-bold text-primary", valueClass)}>{value}</p>
    </div>
  );
}

// Confidence waterfall — shows each boost step
function ConfidencePipeline({ indicators }: { indicators: Record<string, unknown> | null }) {
  const setup = (indicators?.setup as Record<string, unknown>) ?? {};
  const memAdj = (setup.memory_adjustment as Record<string, unknown>) ?? {};

  const rawConf   = Number(indicators?.confidence_raw ?? 0);
  const catalyst  = Number(indicators?.confidence_after_catalyst ?? 0) - rawConf;
  const memory    = Number(memAdj.boost_amount ?? memAdj.calibration_delta ?? 0);
  const rs        = Number(indicators?.rs_boost ?? 0);
  const insider   = Number(indicators?.insider_boost ?? 0);
  const options   = Number(indicators?.options_flow_boost ?? 0);
  const si        = Number(indicators?.short_interest_boost ?? 0);
  const news      = Number(indicators?.news_sentiment_boost ?? 0);

  const steps = [
    { label: "Claude base",     value: rawConf,  isBase: true },
    { label: "Catalyst",        value: catalyst, isBase: false },
    { label: "Memory",          value: memory,   isBase: false },
    { label: "Rel. Strength",   value: rs,       isBase: false },
    { label: "Insider",         value: insider,  isBase: false },
    { label: "Options Flow",    value: options,  isBase: false },
    { label: "Short Interest",  value: si,       isBase: false },
    { label: "News",            value: news,     isBase: false },
  ];

  let running = 0;
  const withRunning = steps.map((s) => {
    running += s.value;
    return { ...s, running };
  });

  return (
    <div className="space-y-1.5">
      {withRunning.map((s, i) => (
        <div key={i} className="flex items-center gap-3">
          <p className="w-28 shrink-0 text-[10px] text-muted">{s.label}</p>
          {s.isBase ? (
            <div className="flex-1 overflow-hidden rounded bg-white/[0.06]">
              <div
                className="h-1.5 rounded bg-cyan/60"
                style={{ width: `${Math.min(s.running * 100, 100)}%` }}
              />
            </div>
          ) : (
            <div className="flex-1 overflow-hidden rounded bg-white/[0.06]">
              <div
                className={cn(
                  "h-1.5 rounded",
                  s.value > 0 ? "bg-success/70" : s.value < 0 ? "bg-danger/70" : "bg-white/10"
                )}
                style={{ width: `${Math.min(Math.abs(s.value) * 100 * 8, 100)}%` }}
              />
            </div>
          )}
          <p className={cn("w-12 shrink-0 text-right font-space text-[10px] font-semibold", s.isBase ? "text-cyan" : boostColor(s.value))}>
            {s.isBase ? conf(s.running) : pct(s.value)}
          </p>
        </div>
      ))}
      <div className="mt-2 flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2">
        <p className="text-[10px] font-bold text-muted">Final Confidence</p>
        <p className="font-space text-sm font-bold text-primary">{conf(withRunning[withRunning.length - 1]?.running)}</p>
      </div>
    </div>
  );
}

// ─── Intelligence card per signal layer ──────────────────────────────────────

function IntelCard({
  label,
  icon: Icon,
  iconColor,
  children,
  boost,
}: {
  label: string;
  icon: React.ElementType;
  iconColor: string;
  children: React.ReactNode;
  boost?: number;
}) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base/60 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Icon className={cn("h-3 w-3", iconColor)} />
          <p className="text-[10px] font-bold uppercase text-muted">{label}</p>
        </div>
        {boost !== undefined && boost !== 0 && (
          <p className={cn("font-space text-[10px] font-bold", boostColor(boost))}>
            {pct(boost)}
          </p>
        )}
      </div>
      {children}
    </div>
  );
}

// ─── Main Drawer ─────────────────────────────────────────────────────────────

interface SignalDrawerProps {
  signal: Signal | null;
  onClose: () => void;
}

export function SignalDrawer({ signal, onClose }: SignalDrawerProps) {
  const overlayRef = useRef<HTMLDivElement>(null);

  // Close on Escape
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!signal) return null;

  const ind = (signal.indicators ?? {}) as Record<string, unknown>;
  const setup = (ind.setup as Record<string, unknown>) ?? {};
  const catalyst = (ind.catalyst as Record<string, unknown>) ?? {};
  const rs = (ind.relative_strength as Record<string, unknown>) ?? {};
  const insider = (ind.insider_flow as Record<string, unknown>) ?? {};
  const options = (ind.options_flow as Record<string, unknown>) ?? {};
  const shortInt = (ind.short_interest as Record<string, unknown>) ?? {};
  const ro = (ind.risk_officer as Record<string, unknown>) ?? {};
  const regime = (signal.market_regime ?? {}) as Record<string, unknown>;
  const outcome = signal.signal_outcomes?.[0];

  const sig = signal.signal.toUpperCase() as "BUY" | "SELL" | "HOLD";
  const isApproved = ro.approved !== false;

  const firedCatalysts = (catalyst.fired as string[] | undefined) ?? [];
  const riskFlags = (ro.risk_flags as string[] | undefined) ?? [];

  return (
    <AnimatePresence>
      {signal && (
        <>
          {/* Backdrop */}
          <motion.div
            ref={overlayRef}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
            onClick={onClose}
          />

          {/* Drawer panel */}
          <motion.aside
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 30, stiffness: 300 }}
            className="fixed right-0 top-0 z-50 flex h-full w-full max-w-[480px] flex-col overflow-hidden border-l border-white/[0.08] bg-[#0a0a0f]"
            onClick={(e) => e.stopPropagation()}
          >
            {/* ── Header ── */}
            <div className="flex shrink-0 items-center justify-between border-b border-white/[0.06] px-5 py-4">
              <div className="flex items-center gap-3">
                <div>
                  <p className="font-space text-xl font-bold text-primary">{signal.symbol}</p>
                  <p className="text-xs text-muted">{new Date(signal.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</p>
                </div>
                <TradeBadge signal={sig} size="md" glow />
              </div>
              <button
                onClick={onClose}
                className="rounded-lg p-1.5 text-muted transition-colors hover:bg-white/[0.06] hover:text-primary"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* ── Scrollable content ── */}
            <div className="flex-1 space-y-5 overflow-y-auto px-5 pb-8 pt-5">

              {/* Risk Officer verdict — shown prominently at top */}
              <div className={cn(
                "flex items-start gap-3 rounded-xl border p-4",
                isApproved
                  ? "border-success/20 bg-success/[0.05]"
                  : "border-danger/20 bg-danger/[0.05]"
              )}>
                {isApproved
                  ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                  : <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" />}
                <div>
                  <p className={cn("text-xs font-bold", isApproved ? "text-success" : "text-danger")}>
                    Risk Officer: {isApproved ? "APPROVED" : "VETOED"}
                  </p>
                  <p className="mt-0.5 text-xs text-muted">
                    {String(ro.officer_note ?? ro.veto_reason ?? (isApproved ? "Trade passed all risk checks." : "Trade was rejected."))}
                  </p>
                  {riskFlags.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {riskFlags.map((f, i) => (
                        <span key={i} className="rounded bg-danger/10 px-1.5 py-0.5 text-[9px] text-danger">{f}</span>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Trade levels */}
              <DrawerSection title="Trade Levels" icon={Target} iconColor="text-cyan">
                <div className="grid grid-cols-2 gap-2">
                  <MiniStat label="Entry"        value={price(signal.entry_price)} />
                  <MiniStat label="Stop"         value={price(signal.stop_loss)} valueClass="text-danger" />
                  <MiniStat label="Target"       value={price(signal.take_profit)} valueClass="text-success" />
                  <MiniStat label="R/R"          value={signal.risk_reward_ratio ? `${signal.risk_reward_ratio.toFixed(1)}:1` : "–"} />
                  <MiniStat label="Confidence"   value={conf(signal.confidence)} />
                  <MiniStat label="Criteria"     value={`${signal.criteria_met ?? "–"}/7`} />
                </div>
              </DrawerSection>

              {/* AI Reasoning */}
              {signal.ai_reasoning && (
                <DrawerSection title="Claude Reasoning" icon={BrainCircuit} iconColor="text-purple">
                  <p className="rounded-xl border border-white/[0.06] bg-base/60 p-3 text-xs leading-relaxed text-primary/90">
                    {signal.ai_reasoning}
                  </p>
                </DrawerSection>
              )}

              {/* Confidence pipeline */}
              <DrawerSection title="Confidence Pipeline" icon={Layers} iconColor="text-cyan">
                <ConfidencePipeline indicators={ind} />
              </DrawerSection>

              {/* Setup */}
              {(setup.type != null || setup.quality != null) && (
                <DrawerSection title="Setup Classification" icon={BarChart3} iconColor="text-success">
                  <div className="grid grid-cols-3 gap-2">
                    <MiniStat label="Type"    value={String(setup.type ?? "–")} />
                    <MiniStat label="Quality" value={setup.quality ? `${(Number(setup.quality) * 100).toFixed(0)}%` : "–"} />
                    <MiniStat label="Min Conf" value={conf(setup.min_confidence_required)} />
                  </div>
                  {setup.memory_adjustment != null && (
                    <p className="mt-2 text-[10px] text-muted">
                      Memory: {String((setup.memory_adjustment as Record<string,unknown>).memory_note ?? "–")}
                    </p>
                  )}
                </DrawerSection>
              )}

              {/* Catalyst */}
              {catalyst.total_score !== undefined && (
                <DrawerSection title="Catalyst Stack" icon={Zap} iconColor="text-amber-300">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-xs text-muted">Total score</p>
                    <p className="font-space text-sm font-bold text-amber-300">{score(catalyst.total_score)}</p>
                  </div>
                  {firedCatalysts.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {firedCatalysts.map((c, i) => (
                        <span key={i} className="rounded bg-amber-300/10 px-2 py-0.5 text-[10px] font-medium text-amber-300">{c}</span>
                      ))}
                    </div>
                  )}
                  {catalyst.dominant != null && (
                    <p className="mt-2 text-[10px] text-muted">Dominant: <span className="text-primary">{String(catalyst.dominant)}</span></p>
                  )}
                </DrawerSection>
              )}

              {/* Intelligence layers */}
              <DrawerSection title="Signal Intelligence" icon={Activity} iconColor="text-purple">
                <div className="space-y-2">
                  {/* Relative Strength */}
                  {Object.keys(rs).length > 0 && (
                    <IntelCard label="Relative Strength" icon={TrendingUp} iconColor="text-cyan" boost={Number(ind.rs_boost ?? 0)}>
                      <div className="grid grid-cols-3 gap-1">
                        <div className="text-center">
                          <p className="text-[9px] text-muted">Signal</p>
                          <p className="font-space text-[10px] font-bold text-primary">{String(rs.rs_signal ?? "–").toUpperCase()}</p>
                        </div>
                        <div className="text-center">
                          <p className="text-[9px] text-muted">RS Score</p>
                          <p className="font-space text-[10px] font-bold text-primary">{score(rs.rs_score ?? rs.score)}</p>
                        </div>
                        <div className="text-center">
                          <p className="text-[9px] text-muted">Rank</p>
                          <p className="font-space text-[10px] font-bold text-primary">{rs.rank_pct ? `${(Number(rs.rank_pct) * 100).toFixed(0)}%` : "–"}</p>
                        </div>
                      </div>
                    </IntelCard>
                  )}

                  {/* Options Flow */}
                  {Object.keys(options).length > 0 && (
                    <IntelCard label="Options Flow" icon={Activity} iconColor="text-purple" boost={Number(ind.options_flow_boost ?? 0)}>
                      <div className="grid grid-cols-3 gap-1">
                        <div className="text-center">
                          <p className="text-[9px] text-muted">Direction</p>
                          <p className={cn("font-space text-[10px] font-bold", options.flow_signal === "bullish" ? "text-success" : options.flow_signal === "bearish" ? "text-danger" : "text-muted")}>
                            {String(options.flow_signal ?? "–").toUpperCase()}
                          </p>
                        </div>
                        <div className="text-center">
                          <p className="text-[9px] text-muted">Conviction</p>
                          <p className="font-space text-[10px] font-bold text-primary">{conf(options.conviction_score)}</p>
                        </div>
                        <div className="text-center">
                          <p className="text-[9px] text-muted">C/P Ratio</p>
                          <p className="font-space text-[10px] font-bold text-primary">{options.call_put_ratio ? Number(options.call_put_ratio).toFixed(2) : "–"}</p>
                        </div>
                      </div>
                      {options.summary != null && (
                        <p className="mt-1.5 text-[10px] text-muted">{String(options.summary)}</p>
                      )}
                    </IntelCard>
                  )}

                  {/* Short Interest */}
                  {Object.keys(shortInt).length > 0 && (
                    <IntelCard label="Short Interest" icon={Flame} iconColor="text-amber-300" boost={Number(ind.short_interest_boost ?? 0)}>
                      <div className="grid grid-cols-3 gap-1">
                        <div className="text-center">
                          <p className="text-[9px] text-muted">Signal</p>
                          <p className={cn("font-space text-[10px] font-bold", shortInt.squeeze_signal === "extreme" ? "text-danger" : shortInt.squeeze_signal === "high" ? "text-amber-300" : "text-muted")}>
                            {String(shortInt.squeeze_signal ?? "–").toUpperCase()}
                          </p>
                        </div>
                        <div className="text-center">
                          <p className="text-[9px] text-muted">Short Float</p>
                          <p className="font-space text-[10px] font-bold text-primary">
                            {shortInt.short_float_pct ? `${Number(shortInt.short_float_pct).toFixed(1)}%` : "–"}
                          </p>
                        </div>
                        <div className="text-center">
                          <p className="text-[9px] text-muted">Days Cover</p>
                          <p className="font-space text-[10px] font-bold text-primary">
                            {shortInt.short_ratio ? Number(shortInt.short_ratio).toFixed(1) : "–"}
                          </p>
                        </div>
                      </div>
                    </IntelCard>
                  )}

                  {/* Insider Flow */}
                  {Object.keys(insider).length > 0 && insider.signal !== "neutral" && (
                    <IntelCard label="Insider Flow (Form 4)" icon={Wallet} iconColor="text-success" boost={Number(ind.insider_boost ?? 0)}>
                      <div className="grid grid-cols-3 gap-1">
                        <div className="text-center">
                          <p className="text-[9px] text-muted">Signal</p>
                          <p className={cn("font-space text-[10px] font-bold", insider.signal === "strong_buy" ? "text-success" : "text-cyan")}>
                            {String(insider.signal ?? "–").replace("_", " ").toUpperCase()}
                          </p>
                        </div>
                        <div className="text-center">
                          <p className="text-[9px] text-muted">Conviction</p>
                          <p className="font-space text-[10px] font-bold text-primary">{conf(insider.conviction_score)}</p>
                        </div>
                        <div className="text-center">
                          <p className="text-[9px] text-muted">Purchases</p>
                          <p className="font-space text-[10px] font-bold text-primary">{String(insider.purchase_count ?? "–")}</p>
                        </div>
                      </div>
                      {insider.summary != null && (
                        <p className="mt-1.5 text-[10px] text-muted">{String(insider.summary)}</p>
                      )}
                    </IntelCard>
                  )}
                </div>
              </DrawerSection>

              {/* Market Regime */}
              {Object.keys(regime).length > 0 && (
                <DrawerSection title="Market Regime" icon={Route} iconColor="text-primary">
                  <div className="grid grid-cols-2 gap-2">
                    <MiniStat label="Regime" value={String(regime.advanced_regime ?? regime.regime ?? "–")} />
                    <MiniStat label="SPY Trend" value={String(regime.spy_trend ?? "–")} />
                    <MiniStat label="SPY RSI" value={regime.spy_rsi ? Number(regime.spy_rsi).toFixed(1) : "–"} />
                    <MiniStat label="VIX Signal" value={String(regime.vix_proxy_signal ?? regime.vix_signal ?? "–")} />
                  </div>
                  {regime.regime_note != null && (
                    <p className="mt-2 text-[10px] text-muted">{String(regime.regime_note)}</p>
                  )}
                </DrawerSection>
              )}

              {/* Outcome (if graded) */}
              {outcome && (
                <DrawerSection title="Graded Outcome" icon={Target} iconColor="text-success">
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <MiniStat label="1h" value={formatPercent(outcome.return_1h_pct ?? 0, 2)} valueClass={pnlColor(outcome.return_1h_pct ?? 0)} />
                    <MiniStat label="1d" value={formatPercent(outcome.return_1d_pct ?? 0, 2)} valueClass={pnlColor(outcome.return_1d_pct ?? 0)} />
                    <MiniStat label="3d" value={formatPercent(outcome.return_3d_pct ?? 0, 2)} valueClass={pnlColor(outcome.return_3d_pct ?? 0)} />
                    <MiniStat label="Score" value={score(outcome.outcome_score)} valueClass={pnlColor(outcome.outcome_score ?? 0)} />
                  </div>
                  <div className="mt-2 flex items-center justify-between text-[10px] text-muted">
                    <span className="flex items-center gap-1">
                      {outcome.hit_take_profit
                        ? <><CheckCircle2 className="h-3 w-3 text-success" /> Target hit</>
                        : outcome.hit_stop
                        ? <><XCircle className="h-3 w-3 text-danger" /> Stop hit</>
                        : <><AlertTriangle className="h-3 w-3 text-amber-300" /> Still open</>}
                    </span>
                    <span>Best {formatPercent(outcome.max_favorable_pct ?? 0, 2)} · Worst {formatPercent(outcome.max_adverse_pct ?? 0, 2)}</span>
                  </div>
                </DrawerSection>
              )}

              {/* Strategy tag */}
              <p className="pt-2 text-center text-[10px] text-muted/40">{signal.strategy} · {signal.id.slice(0, 8)}</p>
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
