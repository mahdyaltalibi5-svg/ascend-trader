"""
catalyst_stack.py — Unified Catalyst Scoring Engine for Ascend Trader Elite

Every stock gets a CatalystScore from multiple independent evidence streams.
High catalyst scores separate high-conviction setups from noise.

Catalyst evidence streams:
  1. Earnings proximity    — imminent earnings = volatility catalyst
  2. Institutional flow    — smart money accumulation / distribution
  3. News sentiment        — headline tone analysis
  4. Volume catalyst       — unusual participation = institutional involvement
  5. Momentum alignment    — trend confirmation
  6. Setup quality         — technical setup classification score
  7. RS leadership         — relative strength vs market
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bullish / bearish word lists for news scoring
# ---------------------------------------------------------------------------

BULLISH_WORDS = frozenset({
    "surge", "surges", "surging",
    "beat", "beats", "beating",
    "upgrade", "upgraded", "upgrades",
    "record", "records",
    "breakout", "breaks out",
    "bullish",
    "strong", "strength",
    "rally", "rallies", "rallying",
    "outperform", "outperforms",
    "buy",
    "positive",
    "boost", "boosted",
    "accelerate", "acceleration",
    "raises", "raised", "raise",
    "growth",
    "soars", "soar", "soaring",
    "tops", "topped",
})

BEARISH_WORDS = frozenset({
    "miss", "misses", "missed",
    "downgrade", "downgraded", "downgrades",
    "warning", "warns", "warned",
    "decline", "declines", "declining",
    "weak", "weakness",
    "concern", "concerns",
    "cut", "cuts",
    "sell",
    "negative",
    "drop", "drops", "dropping",
    "fall", "falls", "falling",
    "loss", "losses",
    "underperform", "underperforms",
    "disappoints", "disappoint", "disappointing",
    "lowers", "lowered", "lower",
    "risk", "risks",
})

# Catalyst component weights
WEIGHTS: dict[str, float] = {
    "earnings_proximity":         2.0,
    "institutional_accumulation": 2.0,
    "news_sentiment":             1.5,
    "volume_catalyst":            1.0,
    "momentum_alignment":         1.0,
    "setup_quality":              0.8,
    "rs_leadership":              0.7,
}

MAX_POSSIBLE_SCORE = sum(WEIGHTS.values())  # 10.0


# ---------------------------------------------------------------------------
# CatalystScore dataclass
# ---------------------------------------------------------------------------

@dataclass
class CatalystScore:
    symbol:             str
    total_score:        float               # 0-10 weighted sum
    components:         dict[str, float]    # individual 0-1 scores
    fired_catalysts:    list[str]           # human-readable active catalysts
    dominant_catalyst:  str                 # single biggest catalyst name
    catalyst_note:      str                 # one sentence for Claude


# ---------------------------------------------------------------------------
# Individual component scorers
# ---------------------------------------------------------------------------

def score_news_sentiment(news: list[dict]) -> float:
    """
    Score news sentiment 0-1 based on bullish / bearish word counts.

    Each headline is tokenized (lowercased, split on whitespace and punctuation).
    Returns 0.5 (neutral) if the news list is empty or all scores cancel out.
    """
    if not news:
        return 0.5

    bullish_hits = 0
    bearish_hits = 0

    for item in news:
        # Support both dict with "headline"/"title" key and plain strings
        if isinstance(item, dict):
            text = item.get("headline", item.get("title", item.get("summary", "")))
        else:
            text = str(item)

        words = set(text.lower().replace(",", " ").replace(".", " ").split())
        bullish_hits += len(words & BULLISH_WORDS)
        bearish_hits += len(words & BEARISH_WORDS)

    total = bullish_hits + bearish_hits
    if total == 0:
        return 0.5

    raw = bullish_hits / total            # 0-1, 0.5 = balanced
    return round(raw, 4)


def _score_earnings_proximity(earnings_intel: dict, symbol: str) -> tuple[float, str | None]:
    """
    Returns (score 0-1, fired_catalyst_str or None).

    Looks for the symbol in earnings_intel dict, which is expected to contain
    a "days_to_earnings" key (int) or be the intel dict keyed by symbol.
    """
    if not earnings_intel:
        return 0.0, None

    # Support two formats: { symbol: {...} } or flat dict with "days_to_earnings"
    sym_data = earnings_intel.get(symbol, earnings_intel)
    days = sym_data.get("days_to_earnings", None)

    if days is None:
        return 0.0, None

    try:
        days = int(days)
    except (TypeError, ValueError):
        return 0.0, None

    if days < 0:
        return 0.0, None
    if days <= 2:
        return 1.0, f"Earnings in {days}d (imminent catalyst)"
    if days <= 5:
        return 0.7, f"Earnings in {days}d (near-term catalyst)"
    if days <= 10:
        return 0.3, f"Earnings in {days}d (approaching)"
    return 0.0, None


def _score_institutional(institutional_intel: dict, symbol: str) -> tuple[float, str | None]:
    """Returns (score 0-1, fired_catalyst_str or None)."""
    if not institutional_intel:
        return 0.0, None

    sym_data = institutional_intel.get(symbol, {})
    if not sym_data:
        return 0.0, None

    if hasattr(sym_data, "__dict__"):
        flow = str(getattr(sym_data, "flow", getattr(sym_data, "signal", ""))).lower()
        conviction = float(
            getattr(sym_data, "conviction", getattr(sym_data, "conviction_score", 0.0)) or 0.0
        )
    else:
        flow = str(sym_data.get("flow", sym_data.get("signal", ""))).lower()
        conviction = float(sym_data.get("conviction", sym_data.get("conviction_score", 0.0)) or 0.0)

    accumulating = any(w in flow for w in ("accumulating", "buying", "accumulate", "buy"))
    distributing = any(w in flow for w in ("distributing", "selling", "distribute", "sell"))

    if distributing:
        return 0.0, "Institutional distribution detected"
    if accumulating and conviction > 0.7:
        return 1.0, f"Institutional accumulation (conviction {conviction:.0%})"
    if accumulating:
        return 0.5, "Institutional accumulation"
    return 0.0, None


def _score_volume(ind_1h: dict) -> tuple[float, str | None]:
    """Returns (score 0-1, fired_catalyst_str or None)."""
    vol_ratio = float(ind_1h.get("volume_ratio", 1.0) or 1.0)
    score = min((vol_ratio - 1.0) / 3.0, 1.0)
    score = max(score, 0.0)
    if score >= 0.33:
        return score, f"Volume surge {vol_ratio:.1f}x average"
    return score, None


def _score_momentum(ind_1h: dict, ind_1d: dict) -> tuple[float, str | None]:
    """Returns (score 0-1, fired_catalyst_str or None)."""
    def _trend(ind: dict) -> str:
        return str(ind.get("ema_trend", ind.get("trend", "neutral")) or "neutral").lower()

    trend_1h = _trend(ind_1h)
    trend_1d = _trend(ind_1d)

    strong_trends = ("strong_bull", "strong_bear", "strongly_bullish", "strongly_bearish")
    mild_trends   = ("bullish", "bearish", "mild_bull", "mild_bear", "mildly_bullish", "mildly_bearish")

    # Both timeframes agree on strong trend
    if trend_1h in strong_trends and trend_1d in strong_trends:
        return 1.0, f"Strong momentum aligned ({trend_1d} daily, {trend_1h} intraday)"
    if trend_1h in strong_trends or trend_1d in strong_trends:
        return 0.8, f"Strong momentum ({trend_1d}/{trend_1h})"
    if trend_1h in mild_trends or trend_1d in mild_trends:
        return 0.6, f"Mild momentum ({trend_1d}/{trend_1h})"
    return 0.0, None


def _score_setup_quality(ind_1d: dict, setup_type: str) -> tuple[float, str | None]:
    """
    Returns (score 0-1, fired_catalyst_str or None).

    Uses 'setup_score' from ind_1d if available, otherwise maps setup_type to a default.
    """
    # Try direct score first
    raw_score = ind_1d.get("setup_score", None)
    if raw_score is not None:
        try:
            score = float(raw_score)
            score = max(0.0, min(score, 1.0))
            if score >= 0.6:
                return score, f"Quality setup: {setup_type} (score {score:.2f})"
            return score, None
        except (TypeError, ValueError):
            pass

    # Fallback: map known setup types to rough quality scores
    setup_defaults: dict[str, float] = {
        "breakout":                   0.80,
        "gap_and_go":                 0.80,
        "earnings_drift":             0.75,
        "momentum_squeeze":           0.75,
        "pullback_continuation":      0.70,
        "news_momentum":              0.65,
        "failed_breakdown_reversal":  0.60,
        "mean_reversion":             0.50,
        "unknown":                    0.30,
    }
    score = setup_defaults.get(setup_type, 0.40)
    if score >= 0.6:
        return score, f"Quality setup: {setup_type}"
    return score, None


def _score_rs_leadership(rs_intel: dict, symbol: str) -> tuple[float, str | None]:
    """Returns (score 0-1, fired_catalyst_str or None)."""
    if not rs_intel:
        return 0.5, None

    info = rs_intel.get(symbol, {})
    signal = str(info.get("rs_signal", "neutral")).lower()
    rank   = float(info.get("rs_rank", 0.5) or 0.5)

    if signal == "leader":
        return 1.0, f"RS leader (rank {int(rank*100)}th percentile)"
    if signal == "laggard":
        return 0.0, f"RS laggard (rank {int(rank*100)}th percentile)"
    return 0.5, None


# ---------------------------------------------------------------------------
# Main catalyst builder
# ---------------------------------------------------------------------------

def build_catalyst_score(
    symbol: str,
    ind_1h: dict,
    ind_1d: dict,
    news: list[dict],
    earnings_intel: dict,
    institutional_intel: dict,
    rs_intel: dict,
    setup_type: str,
    regime: str,
) -> CatalystScore:
    """
    Assemble a CatalystScore from all evidence streams.

    Parameters
    ----------
    symbol            : ticker symbol
    ind_1h            : 1-hour technical indicators dict
    ind_1d            : daily technical indicators dict
    news              : list of news dicts (with "headline" or "title" key)
    earnings_intel    : earnings proximity dict (keyed by symbol or flat with "days_to_earnings")
    institutional_intel: institutional flow dict (keyed by symbol)
    rs_intel          : relative strength intel dict (output of get_relative_strength_intel)
    setup_type        : string from setup_classifier (e.g. "breakout", "gap_and_go")
    regime            : market regime string (e.g. "bull_trend", "bear_trend")
    """
    # Score each component
    earn_score, earn_catalyst  = _score_earnings_proximity(earnings_intel, symbol)
    inst_score, inst_catalyst  = _score_institutional(institutional_intel, symbol)
    news_score                 = score_news_sentiment(news)
    vol_score,  vol_catalyst   = _score_volume(ind_1h)
    mom_score,  mom_catalyst   = _score_momentum(ind_1h, ind_1d)
    setup_score, setup_catalyst = _score_setup_quality(ind_1d, setup_type)
    rs_score,   rs_catalyst    = _score_rs_leadership(rs_intel, symbol)

    components: dict[str, float] = {
        "earnings_proximity":         round(earn_score, 4),
        "institutional_accumulation": round(inst_score, 4),
        "news_sentiment":             round(news_score, 4),
        "volume_catalyst":            round(vol_score,  4),
        "momentum_alignment":         round(mom_score,  4),
        "setup_quality":              round(setup_score, 4),
        "rs_leadership":              round(rs_score,   4),
    }

    # Weighted total — max is 10.0
    weighted_sum = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)
    total_score  = round(min(weighted_sum, 10.0), 3)

    # Collect fired catalysts (non-None strings)
    raw_fired = [earn_catalyst, inst_catalyst, vol_catalyst, mom_catalyst, setup_catalyst, rs_catalyst]
    if news_score >= 0.65:
        raw_fired.append(f"Bullish news sentiment ({news_score:.0%} positive)")
    elif news_score <= 0.35:
        raw_fired.append(f"Bearish news sentiment ({news_score:.0%} positive)")

    fired_catalysts = [c for c in raw_fired if c]

    # Dominant catalyst: the component with highest weighted contribution
    weighted_contribs = {k: components[k] * WEIGHTS[k] for k in WEIGHTS}
    dominant_key      = max(weighted_contribs, key=lambda k: weighted_contribs[k])
    dominant_catalyst = dominant_key.replace("_", " ").title()

    # One-sentence catalyst note
    if total_score >= 7.0:
        conviction = "HIGH-CONVICTION setup"
    elif total_score >= 5.0:
        conviction = "MODERATE-CONVICTION setup"
    elif total_score >= 4.0:
        conviction = "MARGINAL setup"
    else:
        conviction = "LOW-CONVICTION — below trading threshold"

    n_catalysts = len(fired_catalysts)
    note = (
        f"{symbol}: {conviction} with catalyst score {total_score:.1f}/10 "
        f"({n_catalysts} active catalyst{'s' if n_catalysts != 1 else ''}; "
        f"dominant: {dominant_catalyst})."
    )

    return CatalystScore(
        symbol           = symbol,
        total_score      = total_score,
        components       = components,
        fired_catalysts  = fired_catalysts,
        dominant_catalyst= dominant_catalyst,
        catalyst_note    = note,
    )


# ---------------------------------------------------------------------------
# Confidence boost
# ---------------------------------------------------------------------------

def catalyst_confidence_boost(catalyst_score: CatalystScore, raw_confidence: float) -> float:
    """
    Adjust raw Claude confidence based on total catalyst score.

      score > 7  → +0.08
      score > 5  → +0.04
      score < 3  → -0.05
      score < 2  → -0.10
      otherwise  →  0.00

    Returns the adjusted confidence, clamped to [0, 1].
    """
    score = catalyst_score.total_score
    if score > 7.0:
        boost = +0.08
    elif score > 5.0:
        boost = +0.04
    elif score < 2.0:
        boost = -0.10
    elif score < 3.0:
        boost = -0.05
    else:
        boost = 0.0

    return round(max(0.0, min(1.0, raw_confidence + boost)), 4)


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

def build_catalyst_prompt_section(catalyst: CatalystScore) -> str:
    """
    Format catalyst intelligence as a structured block for the Claude prompt.

    Includes total score, active catalysts, component breakdown, and note.
    """
    fired_lines = ""
    if catalyst.fired_catalysts:
        bullets = "\n".join(f"  • {c}" for c in catalyst.fired_catalysts)
        fired_lines = f"\nActive catalysts:\n{bullets}"

    comp = catalyst.components
    component_lines = (
        f"  earnings_proximity={comp['earnings_proximity']:.2f}  "
        f"institutional={comp['institutional_accumulation']:.2f}  "
        f"news_sentiment={comp['news_sentiment']:.2f}  "
        f"volume={comp['volume_catalyst']:.2f}  "
        f"momentum={comp['momentum_alignment']:.2f}  "
        f"setup_quality={comp['setup_quality']:.2f}  "
        f"rs_leadership={comp['rs_leadership']:.2f}"
    )

    threshold = minimum_catalyst_threshold()
    threshold_note = (
        "ABOVE TRADING THRESHOLD" if catalyst.total_score >= threshold
        else "BELOW TRADING THRESHOLD — do not trade"
    )

    return (
        f"CATALYST STACK [{catalyst.symbol}]: "
        f"Score {catalyst.total_score:.1f}/10 — {threshold_note}"
        f"{fired_lines}\n"
        f"Component scores (0-1): {component_lines}\n"
        f"Dominant: {catalyst.dominant_catalyst} | {catalyst.catalyst_note}"
    )


# ---------------------------------------------------------------------------
# Threshold
# ---------------------------------------------------------------------------

def minimum_catalyst_threshold() -> float:
    """
    Minimum CatalystScore.total_score required to consider executing a trade.

    Signals scoring below this are filtered before Claude analysis.
    """
    return 4.0
