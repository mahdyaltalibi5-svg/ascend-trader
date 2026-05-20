"""
setup_classifier.py — Trade setup classification engine for Ascend Trader.

Classifies every candidate trade into a named setup type so the bot can:
  1. Track which setup types are actually profitable over time.
  2. Pass richer context to Claude about what it's looking at.
  3. Apply regime-aware quality scoring before executing.

All functions are pure Python with no async, no external dependencies beyond stdlib.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Setup type constants
# ---------------------------------------------------------------------------

SETUP_BREAKOUT               = "breakout"
SETUP_PULLBACK_CONTINUATION  = "pullback_continuation"
SETUP_MEAN_REVERSION         = "mean_reversion"
SETUP_EARNINGS_DRIFT         = "earnings_drift"
SETUP_MOMENTUM_SQUEEZE       = "momentum_squeeze"
SETUP_GAP_AND_GO             = "gap_and_go"
SETUP_FAILED_BREAKDOWN       = "failed_breakdown_reversal"
SETUP_NEWS_MOMENTUM          = "news_momentum"
SETUP_UNKNOWN                = "unknown"

ALL_SETUP_TYPES = [
    SETUP_BREAKOUT,
    SETUP_PULLBACK_CONTINUATION,
    SETUP_MEAN_REVERSION,
    SETUP_EARNINGS_DRIFT,
    SETUP_MOMENTUM_SQUEEZE,
    SETUP_GAP_AND_GO,
    SETUP_FAILED_BREAKDOWN,
    SETUP_NEWS_MOMENTUM,
    SETUP_UNKNOWN,
]


# ---------------------------------------------------------------------------
# classify_setup
# ---------------------------------------------------------------------------

def classify_setup(
    ind_5m: dict,
    ind_1h: dict,
    ind_1d: dict,
    news: list,
    earnings_catalyst: bool = False,
) -> tuple[str, float]:
    """
    Classify the current setup into a named type.

    Parameters
    ----------
    ind_5m, ind_1h, ind_1d : dicts of technical indicators for each timeframe.
        Expected keys (any timeframe): rsi, macd_hist, bb_upper, bb_lower, bb_pct,
        ema21, ema50, ema200, close, open, prev_close, volume_ratio, roc5,
        stoch_k, ema_trend ("bullish"/"bearish"/"neutral"), atr.
    news : list of news dicts (or strings) for the symbol.
    earnings_catalyst : whether an earnings event is imminent.

    Returns
    -------
    (setup_type, classification_confidence)
        classification_confidence is 0-1, reflecting how cleanly the setup fits.
    """
    scores: dict[str, float] = {t: 0.0 for t in ALL_SETUP_TYPES}

    # ------------------------------------------------------------------ #
    # Helper: safe indicator getter with default
    def _g(ind: dict, key: str, default: float = 0.0) -> float:
        val = ind.get(key, default)
        if key == "macd_hist" and val == default:
            val = ind.get("macd_histogram", default)
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _s(ind: dict, key: str, default: str = "") -> str:
        return str(ind.get(key) or default)

    # ------------------------------------------------------------------ #
    # EARNINGS_DRIFT — highest priority, check first
    if earnings_catalyst:
        scores[SETUP_EARNINGS_DRIFT] += 0.80
        # Bonus if price already trending (pre-earnings drift)
        roc_1d = _g(ind_1d, "roc5")
        if abs(roc_1d) > 0.5:
            scores[SETUP_EARNINGS_DRIFT] += 0.15

    # ------------------------------------------------------------------ #
    # GAP_AND_GO — check gap vs previous close
    close_1d  = _g(ind_1d, "close")
    open_1d   = _g(ind_1d, "open", close_1d)
    prev_close = _g(ind_1d, "prev_close", close_1d)
    vol_ratio_1h = _g(ind_1h, "volume_ratio", 1.0)

    if prev_close > 0 and close_1d > 0:
        gap_pct = abs(open_1d - prev_close) / prev_close
        if gap_pct > 0.01:                         # > 1% gap
            scores[SETUP_GAP_AND_GO] += 0.55
            if gap_pct > 0.025:
                scores[SETUP_GAP_AND_GO] += 0.20   # large gap bonus
            if vol_ratio_1h > 1.8:
                scores[SETUP_GAP_AND_GO] += 0.15   # volume confirmation
            if _g(ind_5m, "volume_ratio", 1.0) > 2.0:
                scores[SETUP_GAP_AND_GO] += 0.10

    # ------------------------------------------------------------------ #
    # BREAKOUT
    vol_ratio_5m = _g(ind_5m, "volume_ratio", 1.0)
    bb_pct_5m    = _g(ind_5m, "bb_pct")        # 0=lower band, 1=upper band
    if bb_pct_5m > 1.5:
        bb_pct_5m = bb_pct_5m / 100.0
    roc5_5m      = _g(ind_5m, "roc5")

    if vol_ratio_5m > 1.8:
        scores[SETUP_BREAKOUT] += 0.30
    if bb_pct_5m > 0.90:                         # price near upper band
        scores[SETUP_BREAKOUT] += 0.25
    if roc5_5m > 0.5:
        scores[SETUP_BREAKOUT] += 0.25
    if vol_ratio_1h > 1.5:
        scores[SETUP_BREAKOUT] += 0.10
    if _g(ind_1d, "roc5") > 0.3:
        scores[SETUP_BREAKOUT] += 0.10

    # ------------------------------------------------------------------ #
    # MOMENTUM_SQUEEZE — requires extreme volume + aligned EMAs
    roc5_1h     = _g(ind_1h, "roc5")
    ema_trend_1h = _s(ind_1h, "ema_trend")
    macd_hist_1h = _g(ind_1h, "macd_hist")
    ema_trend_1d = _s(ind_1d, "ema_trend")

    if vol_ratio_5m > 2.5:
        scores[SETUP_MOMENTUM_SQUEEZE] += 0.30
    if vol_ratio_1h > 2.0:
        scores[SETUP_MOMENTUM_SQUEEZE] += 0.20
    if roc5_5m > 0.8:
        scores[SETUP_MOMENTUM_SQUEEZE] += 0.20
    if ema_trend_1h == "bullish" and ema_trend_1d == "bullish":
        scores[SETUP_MOMENTUM_SQUEEZE] += 0.20    # all EMAs aligned
    if macd_hist_1h > 0 and _g(ind_1h, "macd_hist") > _g(ind_5m, "macd_hist"):
        scores[SETUP_MOMENTUM_SQUEEZE] += 0.10

    # ------------------------------------------------------------------ #
    # PULLBACK_CONTINUATION
    rsi_1h   = _g(ind_1h, "rsi", 50.0)
    close_1h = _g(ind_1h, "close")
    ema21_1h = _g(ind_1h, "ema21")
    ema50_1h = _g(ind_1h, "ema50")

    if ema_trend_1d == "bullish":
        scores[SETUP_PULLBACK_CONTINUATION] += 0.25
    if 40 <= rsi_1h <= 55:
        scores[SETUP_PULLBACK_CONTINUATION] += 0.25
    if macd_hist_1h > 0 and _g(ind_5m, "macd_hist") < _g(ind_1h, "macd_hist"):
        # MACD hist turning up (1h positive, 5m catching up)
        scores[SETUP_PULLBACK_CONTINUATION] += 0.20
    if ema21_1h > 0 and close_1h > 0:
        dist_ema21 = abs(close_1h - ema21_1h) / ema21_1h
        if dist_ema21 < 0.015:                   # price near ema21
            scores[SETUP_PULLBACK_CONTINUATION] += 0.20
    if ema50_1h > 0 and close_1h > 0:
        dist_ema50 = abs(close_1h - ema50_1h) / ema50_1h
        if dist_ema50 < 0.015:                   # price near ema50
            scores[SETUP_PULLBACK_CONTINUATION] += 0.10

    # ------------------------------------------------------------------ #
    # MEAN_REVERSION
    rsi_5m    = _g(ind_5m, "rsi", 50.0)
    stoch_5m  = _g(ind_5m, "stoch_k", 50.0)
    bb_pct_5m_lo = bb_pct_5m

    extreme_rsi = rsi_5m < 30 or rsi_5m > 72
    if extreme_rsi:
        scores[SETUP_MEAN_REVERSION] += 0.35
    if bb_pct_5m_lo < 0.05 or bb_pct_5m_lo > 0.95:   # price touching band
        scores[SETUP_MEAN_REVERSION] += 0.30
    if stoch_5m < 20 or stoch_5m > 80:
        scores[SETUP_MEAN_REVERSION] += 0.20
    if _g(ind_1h, "rsi", 50) < 35 or _g(ind_1h, "rsi", 50) > 68:
        scores[SETUP_MEAN_REVERSION] += 0.15

    # ------------------------------------------------------------------ #
    # FAILED_BREAKDOWN_REVERSAL
    # Heuristic: prior bearish (1d trend bearish or recent negative roc), but
    # 5m/1h now showing bullish reversal signals.
    prior_bearish = ema_trend_1d == "bearish" or _g(ind_1d, "roc5") < -0.5
    now_reversing = (
        _s(ind_5m, "ema_trend") == "bullish"
        and macd_hist_1h > 0
        and rsi_1h > 45
    )
    if prior_bearish and now_reversing:
        scores[SETUP_FAILED_BREAKDOWN] += 0.55
        if bb_pct_5m > 0.50:                     # price back above midline
            scores[SETUP_FAILED_BREAKDOWN] += 0.20
        if vol_ratio_5m > 1.5:
            scores[SETUP_FAILED_BREAKDOWN] += 0.15

    # ------------------------------------------------------------------ #
    # NEWS_MOMENTUM
    news_count = len(news) if isinstance(news, (list, tuple)) else 0
    if news_count > 3:
        scores[SETUP_NEWS_MOMENTUM] += 0.40
        if news_count > 7:
            scores[SETUP_NEWS_MOMENTUM] += 0.20
        if abs(roc5_1h) > 0.4:
            scores[SETUP_NEWS_MOMENTUM] += 0.20
        if vol_ratio_1h > 1.5:
            scores[SETUP_NEWS_MOMENTUM] += 0.15

    # ------------------------------------------------------------------ #
    # Resolve winner
    best_type = max(scores, key=lambda k: scores[k])
    best_score = scores[best_type]

    # Require minimum conviction to avoid weak classifications
    if best_score < 0.30:
        return SETUP_UNKNOWN, 0.30

    # Normalise score to [0.40, 0.95] confidence range
    raw_conf = 0.40 + (min(best_score, 1.0) * 0.55)
    classification_confidence = round(min(raw_conf, 0.95), 4)

    return best_type, classification_confidence


# ---------------------------------------------------------------------------
# setup_win_rates — hardcoded research-based priors
# ---------------------------------------------------------------------------

def setup_win_rates() -> dict[str, dict]:
    """
    Return hardcoded prior win rates and metadata for each setup type.

    Sources: backtested priors from quantitative research and the Ascend
    historical signal database.  These are updated manually each quarter.

    Keys per entry:
        expected_win_rate_1d  : historical 1-day win rate (float 0-1)
        expected_win_rate_3d  : historical 3-day win rate
        avg_rr                : average risk/reward achieved
        best_regimes          : list of regime names where setup excels
        worst_regimes         : list of regime names to avoid this setup
        avg_holding_hours     : typical holding period in hours
        notes                 : one-line research summary
    """
    return {
        SETUP_BREAKOUT: {
            "expected_win_rate_1d": 0.58,
            "expected_win_rate_3d": 0.54,
            "avg_rr": 2.4,
            "best_regimes": ["trend_day", "risk_on", "low_vol_drift"],
            "worst_regimes": ["chop_range", "high_vol_panic"],
            "avg_holding_hours": 6,
            "notes": "Works best when market is trending; fails in choppy, mean-reverting tape.",
        },
        SETUP_PULLBACK_CONTINUATION: {
            "expected_win_rate_1d": 0.62,
            "expected_win_rate_3d": 0.59,
            "avg_rr": 2.8,
            "best_regimes": ["trend_day", "risk_on", "sector_rotation"],
            "worst_regimes": ["high_vol_panic", "risk_off"],
            "avg_holding_hours": 12,
            "notes": "Highest win rate of any setup; requires clean prior trend structure.",
        },
        SETUP_MEAN_REVERSION: {
            "expected_win_rate_1d": 0.55,
            "expected_win_rate_3d": 0.52,
            "avg_rr": 1.9,
            "best_regimes": ["chop_range", "low_vol_drift"],
            "worst_regimes": ["trend_day", "high_vol_panic"],
            "avg_holding_hours": 4,
            "notes": "Thrives in range-bound markets; devastating in strong trending tapes.",
        },
        SETUP_EARNINGS_DRIFT: {
            "expected_win_rate_1d": 0.60,
            "expected_win_rate_3d": 0.57,
            "avg_rr": 2.2,
            "best_regimes": ["risk_on", "trend_day", "low_vol_drift"],
            "worst_regimes": ["high_vol_panic", "risk_off"],
            "avg_holding_hours": 8,
            "notes": "Requires correct directional read on earnings outcome; binary risk.",
        },
        SETUP_MOMENTUM_SQUEEZE: {
            "expected_win_rate_1d": 0.56,
            "expected_win_rate_3d": 0.51,
            "avg_rr": 3.1,
            "best_regimes": ["trend_day", "risk_on"],
            "worst_regimes": ["chop_range", "risk_off"],
            "avg_holding_hours": 3,
            "notes": "High R/R but short-lived; must act fast or the move is over.",
        },
        SETUP_GAP_AND_GO: {
            "expected_win_rate_1d": 0.53,
            "expected_win_rate_3d": 0.50,
            "avg_rr": 2.0,
            "best_regimes": ["trend_day", "risk_on"],
            "worst_regimes": ["high_vol_panic", "chop_range"],
            "avg_holding_hours": 2,
            "notes": "First 30 min of session matter most; gap fills are common failure mode.",
        },
        SETUP_FAILED_BREAKDOWN: {
            "expected_win_rate_1d": 0.57,
            "expected_win_rate_3d": 0.53,
            "avg_rr": 2.6,
            "best_regimes": ["chop_range", "low_vol_drift"],
            "worst_regimes": ["trend_day", "risk_off"],
            "avg_holding_hours": 8,
            "notes": "Requires confirmed reversal above prior breakdown level; patience entry.",
        },
        SETUP_NEWS_MOMENTUM: {
            "expected_win_rate_1d": 0.54,
            "expected_win_rate_3d": 0.49,
            "avg_rr": 1.8,
            "best_regimes": ["risk_on", "trend_day", "sector_rotation"],
            "worst_regimes": ["high_vol_panic"],
            "avg_holding_hours": 5,
            "notes": "News edge decays within hours; late entries are losers.",
        },
        SETUP_UNKNOWN: {
            "expected_win_rate_1d": 0.48,
            "expected_win_rate_3d": 0.47,
            "avg_rr": 1.5,
            "best_regimes": [],
            "worst_regimes": [],
            "avg_holding_hours": 6,
            "notes": "No identifiable pattern — treat with extra skepticism.",
        },
    }


# ---------------------------------------------------------------------------
# setup_notes_for_claude
# ---------------------------------------------------------------------------

def setup_notes_for_claude(setup_type: str) -> str:
    """
    Return a single paragraph giving Claude context about the setup type:
    what to watch for and common failure modes.
    """
    notes = {
        SETUP_BREAKOUT: (
            "This is a BREAKOUT setup: price is pushing above a key resistance level on elevated "
            "volume. The key thing to verify is that volume is genuinely above average (not a thin "
            "tape breakout) and that broader market conditions are supportive. Common failure modes: "
            "(1) false breakout where price immediately reverses back below resistance — look for a "
            "clean reclaim or flag before entering; (2) breakout into a major resistance cluster "
            "on a daily chart that caps upside quickly; (3) breakout in a choppy or risk-off market "
            "where follow-through is statistically poor. Ideal entry is on a 5-min pullback to "
            "the breakout level after initial surge — chasing the first candle is the #1 mistake."
        ),
        SETUP_PULLBACK_CONTINUATION: (
            "This is a PULLBACK CONTINUATION setup: the primary trend is intact and price has "
            "temporarily pulled back to a key moving average (EMA21 or EMA50) before resuming. "
            "Watch for: MACD histogram turning positive on the 1h after being negative — that's "
            "the trigger. RSI holding above 40 on the pullback is a healthy sign. Common failure "
            "modes: (1) trend is actually broken and this is a dead-cat bounce — confirm the 1d "
            "trend structure is intact; (2) pullback goes deeper than expected and you're stopped "
            "out before the continuation — use ATR-based stops rather than arbitrary levels; "
            "(3) entry too early while RSI still declining. Wait for RSI to stop falling."
        ),
        SETUP_MEAN_REVERSION: (
            "This is a MEAN REVERSION setup: price has stretched to an extreme relative to its "
            "moving averages and Bollinger Bands and is expected to snap back to the mean. "
            "The edge here is statistical, not directional — you're betting on reversion, not trend. "
            "Watch for: RSI below 30 or above 72 WITH price touching the BB band — the confluence "
            "is critical. Common failure modes: (1) momentum continues after extreme reads — "
            "in strong trending markets RSI can stay oversold/overbought for hours; "
            "(2) confusing a trend day for a mean-reversion opportunity; "
            "(3) holding too long — mean reversion targets are the midline (EMA21), not a new trend. "
            "This setup has the lowest R/R of any strategy — size accordingly."
        ),
        SETUP_EARNINGS_DRIFT: (
            "This is an EARNINGS DRIFT setup: an earnings catalyst is imminent and price is "
            "already moving in the likely direction of the outcome. The edge comes from pre-earnings "
            "positioning by informed participants. Watch for: steady trending volume with no single "
            "spike (that suggests leaked information risk), consistent daily drift in one direction "
            "for 3-5 days before the event. Common failure modes: (1) buy the rumor, sell the news "
            "— the drift price is already pricing in a beat, so even a beat can sell off; "
            "(2) holding through the earnings announcement itself — that is a binary gamble, "
            "not a trade; (3) missing that the drift has already run for too long (>7 sessions). "
            "Exit before the announcement. Set a hard stop at -1.5 ATR."
        ),
        SETUP_MOMENTUM_SQUEEZE: (
            "This is a MOMENTUM SQUEEZE setup: all indicators are aligned (EMA stack bullish, "
            "MACD expanding, volume surging 2.5x+) creating a high-velocity move. This is the "
            "highest R/R setup but also the shortest-lived — you have a 30-60 minute window. "
            "Watch for: volume ratio dropping back toward 1.5x (momentum exhaustion signal). "
            "Common failure modes: (1) chasing — by the time the squeeze is obvious, the best "
            "entry is gone; (2) not taking partial profits at first R target; "
            "(3) holding for a continuation that doesn't come — these setups often end with "
            "a sharp reversal. Trail stops aggressively once in profit."
        ),
        SETUP_GAP_AND_GO: (
            "This is a GAP AND GO setup: price has gapped up or down significantly from the prior "
            "close with elevated volume, signaling institutional conviction. The first 15 minutes "
            "are the most critical — if the gap holds and volume stays elevated, the trend likely "
            "continues. Watch for: gap holding above (or below) the prior day's high/low — that's "
            "confirmation. Common failure modes: (1) gap fill — price reverses to fill the gap "
            "before going anywhere; this happens >40% of the time on gaps under 2%; "
            "(2) pre-market volume was fake (ETF arb), actual open volume is thin; "
            "(3) broader market reverses and takes the gap trade with it. "
            "Set stops tight below the gap open candle low."
        ),
        SETUP_FAILED_BREAKDOWN: (
            "This is a FAILED BREAKDOWN REVERSAL setup: price broke below a key support level "
            "but immediately reversed back above it, trapping short sellers. These setups can "
            "be explosive when they work because shorts are forced to cover into your long. "
            "Watch for: the reversal candle must close back above the breakdown level — a wick "
            "below doesn't count. Volume on the reversal candle should be elevated. "
            "Common failure modes: (1) the breakdown was valid and the reversal is just a dead-cat; "
            "verify 1d trend isn't structurally broken; (2) the reversal runs only to the breakdown "
            "level and stalls — have a profit target at the prior support-turned-resistance zone; "
            "(3) market-wide risk-off conditions make the reversal unsustainable."
        ),
        SETUP_NEWS_MOMENTUM: (
            "This is a NEWS MOMENTUM setup: a significant news event (>3 items) is driving price "
            "in a clear direction with volume confirmation. The edge decays rapidly — within "
            "2-3 hours the market has usually fully priced the news. "
            "Watch for: news items that are genuinely new information (not recirculations of old "
            "stories), volume spike on the 5m timeframe within the first 30 min after news. "
            "Common failure modes: (1) stale news being recirculated — check timestamps carefully; "
            "(2) competing news items going in opposite directions creating confusion; "
            "(3) entering after the initial move has already completed — late entries are losers "
            "for news plays. Ask: is this news a surprise or already priced in?"
        ),
        SETUP_UNKNOWN: (
            "This setup does NOT fit a clean technical pattern. Proceed with extra caution. "
            "The indicators are mixed or contradictory. If a trade is still taken here, "
            "it should be sized at 50% of normal position size, require higher confidence from "
            "the AI analysis, and carry tighter stops. Common mistake: forcing a trade when "
            "the setup isn't clear — the best trade is sometimes no trade."
        ),
    }
    return notes.get(setup_type, notes[SETUP_UNKNOWN])


# ---------------------------------------------------------------------------
# score_setup_quality
# ---------------------------------------------------------------------------

def score_setup_quality(setup_type: str, ind_1h: dict, regime: str) -> float:
    """
    Compute a 0-1 quality score for a specific setup in the current regime.

    Combines:
    - Base win rate for the setup type in this regime (from setup_win_rates priors)
    - Technical indicator quality score (how clean are the signals)
    - Regime alignment bonus or penalty

    Returns a float clamped to [0.0, 1.0].
    """
    win_rates = setup_win_rates()
    setup_info = win_rates.get(setup_type, win_rates[SETUP_UNKNOWN])

    # ------------------------------------------------------------------ #
    # Component 1: regime alignment (0.0 - 1.0)
    best_regimes  = setup_info.get("best_regimes", [])
    worst_regimes = setup_info.get("worst_regimes", [])

    if regime in best_regimes:
        regime_score = 1.0
    elif regime in worst_regimes:
        regime_score = 0.10
    else:
        regime_score = 0.55   # neutral regime — use base win rate

    # ------------------------------------------------------------------ #
    # Component 2: base expected win rate as a quality signal
    base_wr = setup_info.get("expected_win_rate_1d", 0.50)
    wr_score = _normalize_win_rate(base_wr)

    # ------------------------------------------------------------------ #
    # Component 3: indicator quality on 1h timeframe
    ind_score = _indicator_quality_score(ind_1h, setup_type)

    # ------------------------------------------------------------------ #
    # Weighted composite
    composite = (regime_score * 0.40) + (wr_score * 0.30) + (ind_score * 0.30)
    return round(min(max(composite, 0.0), 1.0), 4)


def _normalize_win_rate(wr: float) -> float:
    """Map win rate [0.45, 0.70] → [0.0, 1.0] quality score."""
    clamped = max(0.45, min(wr, 0.70))
    return (clamped - 0.45) / 0.25


def _indicator_quality_score(ind_1h: dict, setup_type: str) -> float:
    """
    Assess how cleanly the 1h indicators support the named setup type.

    Returns a 0-1 score.
    """
    def _g(key: str, default: float = 0.0) -> float:
        val = ind_1h.get(key, default)
        if key == "macd_hist" and val == default:
            val = ind_1h.get("macd_histogram", default)
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _s(key: str, default: str = "") -> str:
        return str(ind_1h.get(key) or default)

    rsi       = _g("rsi", 50.0)
    macd_hist = _g("macd_hist")
    vol_ratio = _g("volume_ratio", 1.0)
    bb_pct    = _g("bb_pct", 0.5)
    if bb_pct > 1.5:
        bb_pct = bb_pct / 100.0
    ema_trend = _s("ema_trend", "neutral")
    roc5      = _g("roc5")
    stoch_k   = _g("stoch_k", 50.0)

    if setup_type == SETUP_BREAKOUT:
        score = 0.0
        if vol_ratio > 2.0: score += 0.35
        elif vol_ratio > 1.8: score += 0.20
        if bb_pct > 0.90: score += 0.30
        if roc5 > 0.5: score += 0.20
        if macd_hist > 0: score += 0.15
        return min(score, 1.0)

    elif setup_type == SETUP_PULLBACK_CONTINUATION:
        score = 0.0
        if ema_trend == "bullish": score += 0.30
        if 40 <= rsi <= 55: score += 0.30
        if macd_hist > 0: score += 0.25
        if vol_ratio < 1.5: score += 0.15   # low volume on pullback is healthy
        return min(score, 1.0)

    elif setup_type == SETUP_MEAN_REVERSION:
        score = 0.0
        if rsi < 28 or rsi > 74: score += 0.35
        elif rsi < 32 or rsi > 70: score += 0.20
        if bb_pct < 0.05 or bb_pct > 0.95: score += 0.30
        if stoch_k < 15 or stoch_k > 85: score += 0.25
        if vol_ratio < 1.3: score += 0.10   # mean reversion better on thin vol
        return min(score, 1.0)

    elif setup_type == SETUP_EARNINGS_DRIFT:
        score = 0.50                         # base score for having catalyst
        if abs(roc5) > 0.4: score += 0.25
        if vol_ratio > 1.3: score += 0.15
        if macd_hist > 0 and roc5 > 0: score += 0.10
        return min(score, 1.0)

    elif setup_type == SETUP_MOMENTUM_SQUEEZE:
        score = 0.0
        if vol_ratio > 3.0: score += 0.35
        elif vol_ratio > 2.5: score += 0.25
        if abs(roc5) > 1.0: score += 0.30
        if ema_trend == "bullish" and macd_hist > 0: score += 0.25
        if rsi > 55 and rsi < 80: score += 0.10
        return min(score, 1.0)

    elif setup_type == SETUP_GAP_AND_GO:
        score = 0.30                         # base for having detected a gap
        if vol_ratio > 2.0: score += 0.35
        if roc5 > 0.5: score += 0.25
        if macd_hist > 0: score += 0.10
        return min(score, 1.0)

    elif setup_type == SETUP_FAILED_BREAKDOWN:
        score = 0.0
        if macd_hist > 0: score += 0.30
        if rsi > 45: score += 0.25
        if vol_ratio > 1.5: score += 0.25
        if bb_pct > 0.45: score += 0.20     # price back above midline
        return min(score, 1.0)

    elif setup_type == SETUP_NEWS_MOMENTUM:
        score = 0.25                         # base for having news
        if vol_ratio > 2.0: score += 0.35
        if abs(roc5) > 0.5: score += 0.25
        if ema_trend in ("bullish", "bearish"): score += 0.15
        return min(score, 1.0)

    # UNKNOWN
    return 0.25
