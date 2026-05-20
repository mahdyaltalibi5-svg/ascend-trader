import { NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

// ---------------------------------------------------------------------------
// Sector map — symbol → sector
// ---------------------------------------------------------------------------
const SECTOR_MAP: Record<string, string> = {
  AAPL: "Technology",
  MSFT: "Technology",
  NVDA: "Technology",
  AMD:  "Technology",
  GOOG: "Technology",
  GOOGL:"Technology",
  META: "Technology",
  AMZN: "Consumer Discretionary",
  TSLA: "Consumer Discretionary",
  HD:   "Consumer Discretionary",
  JPM:  "Financials",
  BAC:  "Financials",
  GS:   "Financials",
  MS:   "Financials",
  V:    "Financials",
  MA:   "Financials",
  UNH:  "Healthcare",
  JNJ:  "Healthcare",
  PFE:  "Healthcare",
  LLY:  "Healthcare",
  XOM:  "Energy",
  CVX:  "Energy",
  COP:  "Energy",
  CAT:  "Industrials",
  BA:   "Industrials",
  GE:   "Industrials",
  SPY:  "ETF",
  QQQ:  "ETF",
  IWM:  "ETF",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function safeDiv(num: number, den: number, fallback = 0): number {
  return den === 0 ? fallback : num / den;
}

function stdDev(values: number[]): number {
  if (values.length < 2) return 0;
  const mean = values.reduce((s, v) => s + v, 0) / values.length;
  const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance);
}

// ---------------------------------------------------------------------------
// GET /api/analytics
// ---------------------------------------------------------------------------
export async function GET() {
  try {
    const supabase = await createServiceClient();

    // ── Fetch data in parallel ──────────────────────────────────────────────
    const [
      portfolioRes,
      tradesRes,
      snapshotsRes,
      signalsCountRes,
    ] = await Promise.all([
      supabase.from("portfolio").select("equity,daily_pnl").order("updated_at", { ascending: false }).limit(1),
      supabase.from("trades").select("symbol,side,pnl,entry_price,stop_loss,status").order("created_at", { ascending: false }),
      supabase.from("portfolio_snapshots").select("equity,daily_pnl,snapshot_at").order("snapshot_at", { ascending: true }),
      supabase.from("signals").select("id", { count: "exact", head: true }),
    ]);

    // signal_outcomes is optional — try separately, ignore if table missing
    let signalOutcomes: { outcome_score: number | null }[] = [];
    try {
      const { data } = await supabase.from("signal_outcomes").select("outcome_score");
      signalOutcomes = data ?? [];
    } catch {
      // table doesn't exist yet — that's fine
    }

    // ── Portfolio basics ────────────────────────────────────────────────────
    const portfolio = portfolioRes.data?.[0];
    const latestEquity = portfolio?.equity ?? 100_000;
    const dailyPnl     = portfolio?.daily_pnl ?? 0;

    const INITIAL_EQUITY = 100_000;
    const total_return_pct = safeDiv((latestEquity - INITIAL_EQUITY), INITIAL_EQUITY) * 100;
    const day_pnl_pct      = safeDiv(dailyPnl, latestEquity - dailyPnl) * 100;

    // ── Trade metrics ───────────────────────────────────────────────────────
    const allTrades    = tradesRes.data ?? [];
    const closedTrades = allTrades.filter((t) => t.status === "closed" && t.pnl !== null);
    const wins         = closedTrades.filter((t) => (t.pnl ?? 0) > 0);
    const losses       = closedTrades.filter((t) => (t.pnl ?? 0) < 0);

    const win_rate   = safeDiv(wins.length, closedTrades.length) * 100;
    const total_trades = allTrades.length;

    // R-multiples: use stop distance from entry_price - stop_loss (fallback 1)
    const avgWinRaw = safeDiv(wins.reduce((s, t) => s + (t.pnl ?? 0), 0), wins.length || 1);
    const avgLossRaw = safeDiv(losses.reduce((s, t) => s + (t.pnl ?? 0), 0), losses.length || 1);

    // avg stop distance
    const stopDistances = closedTrades
      .map((t) => {
        if (t.stop_loss && t.entry_price) return Math.abs(t.entry_price - t.stop_loss);
        return 1;
      });
    const avgStop = safeDiv(stopDistances.reduce((s, v) => s + v, 0), stopDistances.length || 1, 1);

    const avg_win_r  = safeDiv(avgWinRaw, avgStop);
    const avg_loss_r = safeDiv(Math.abs(avgLossRaw), avgStop);

    const sumWins   = wins.reduce((s, t) => s + (t.pnl ?? 0), 0);
    const sumLosses = Math.abs(losses.reduce((s, t) => s + (t.pnl ?? 0), 0));
    const profit_factor = safeDiv(sumWins, sumLosses);

    // Best / worst trades
    const sortedByPnl = [...closedTrades].sort((a, b) => (b.pnl ?? 0) - (a.pnl ?? 0));
    const best_trade  = sortedByPnl[0]
      ? { symbol: sortedByPnl[0].symbol, pnl: sortedByPnl[0].pnl, side: sortedByPnl[0].side }
      : null;
    const worst_trade = sortedByPnl[sortedByPnl.length - 1]
      ? {
          symbol: sortedByPnl[sortedByPnl.length - 1].symbol,
          pnl:    sortedByPnl[sortedByPnl.length - 1].pnl,
          side:   sortedByPnl[sortedByPnl.length - 1].side,
        }
      : null;

    // ── Portfolio snapshots ─────────────────────────────────────────────────
    const snapshots = snapshotsRes.data ?? [];

    // Last 90 days
    const cutoff90 = new Date();
    cutoff90.setDate(cutoff90.getDate() - 90);
    const recent90 = snapshots.filter((s) => new Date(s.snapshot_at) >= cutoff90);

    // Equity curve (one point per day — pick latest per day)
    const equityByDay: Record<string, number> = {};
    for (const s of recent90) {
      const day = s.snapshot_at.slice(0, 10);
      equityByDay[day] = s.equity; // last write wins (ascending order means latest)
    }
    const equity_curve = Object.entries(equityByDay)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, equity]) => ({ date, equity }));

    // Daily pnl series
    const pnlByDay: Record<string, number> = {};
    for (const s of recent90) {
      const day = s.snapshot_at.slice(0, 10);
      pnlByDay[day] = s.daily_pnl;
    }
    const daily_pnl_series = Object.entries(pnlByDay)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, daily_pnl]) => {
        // compute pct from equity
        const eq = equityByDay[date] ?? latestEquity;
        const pnl_pct = safeDiv(daily_pnl, eq - daily_pnl) * 100;
        return { date, pnl_pct };
      });

    // Sharpe — approximate from daily returns
    const dailyReturns: number[] = [];
    const equityDates = equity_curve.map((e) => e.equity);
    for (let i = 1; i < equityDates.length; i++) {
      const prev = equityDates[i - 1];
      if (prev > 0) dailyReturns.push((equityDates[i] - prev) / prev);
    }
    const meanReturn = safeDiv(dailyReturns.reduce((s, v) => s + v, 0), dailyReturns.length);
    const returnStd  = stdDev(dailyReturns);
    const sharpe     = returnStd > 0 ? (meanReturn / returnStd) * Math.sqrt(252) : 0;

    // Max drawdown from equity curve
    let peak = 0;
    let max_drawdown_pct = 0;
    for (const { equity } of equity_curve) {
      if (equity > peak) peak = equity;
      const dd = peak > 0 ? safeDiv(peak - equity, peak) * 100 : 0;
      if (dd > max_drawdown_pct) max_drawdown_pct = dd;
    }

    // Alpha vs SPY (12% annualized assumed)
    // Approximate days in our sample
    const tradingDays = Math.max(dailyReturns.length, 1);
    const annualizationFactor = 252 / tradingDays;
    const spyReturn = 12 * (1 / annualizationFactor); // scaled to same period
    const alpha_pct = total_return_pct - spyReturn;

    // ── Signals ─────────────────────────────────────────────────────────────
    const signals_fired = signalsCountRes.count ?? 0;

    const accurateSignals = signalOutcomes.filter((o) => (o.outcome_score ?? 0) > 0).length;
    const signal_accuracy = safeDiv(accurateSignals, signalOutcomes.length) * 100;

    // ── By sector ───────────────────────────────────────────────────────────
    const by_sector: Record<string, { trades: number; wins: number; total_pnl: number }> = {};
    for (const t of closedTrades) {
      const sector = SECTOR_MAP[t.symbol] ?? "Other";
      if (!by_sector[sector]) by_sector[sector] = { trades: 0, wins: 0, total_pnl: 0 };
      by_sector[sector].trades   += 1;
      by_sector[sector].wins     += (t.pnl ?? 0) > 0 ? 1 : 0;
      by_sector[sector].total_pnl += t.pnl ?? 0;
    }

    // ── Top 5 trades by PnL ─────────────────────────────────────────────────
    const top_trades = sortedByPnl.slice(0, 5).map((t) => ({
      symbol: t.symbol,
      pnl:    t.pnl,
      side:   t.side,
    }));

    return NextResponse.json({
      total_return_pct,
      day_pnl_pct,
      win_rate,
      avg_win_r,
      avg_loss_r,
      profit_factor,
      total_trades,
      wins_count:  wins.length,
      losses_count: losses.length,
      best_trade,
      worst_trade,
      sharpe,
      max_drawdown_pct,
      alpha_pct,
      signals_fired,
      signal_accuracy,
      by_sector,
      equity_curve,
      daily_pnl_series,
      top_trades,
    });
  } catch (err) {
    console.error("[analytics]", err);
    return NextResponse.json(
      {
        total_return_pct: 0, day_pnl_pct: 0, win_rate: 0,
        avg_win_r: 0, avg_loss_r: 0, profit_factor: 0,
        total_trades: 0, wins_count: 0, losses_count: 0,
        best_trade: null, worst_trade: null,
        sharpe: 0, max_drawdown_pct: 0, alpha_pct: 0,
        signals_fired: 0, signal_accuracy: 0,
        by_sector: {}, equity_curve: [], daily_pnl_series: [], top_trades: [],
      },
      { status: 200 }
    );
  }
}
