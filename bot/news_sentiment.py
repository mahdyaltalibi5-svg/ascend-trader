"""
news_sentiment.py — Claude-powered news headline sentiment scorer for Ascend Trader.

Scores each headline for a given symbol on a -1.0 to +1.0 scale and
returns aggregated sentiment metrics used to adjust trade confidence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache: keyed by symbol, stores (timestamp, list[SentimentScore])
# ---------------------------------------------------------------------------
_sentiment_cache: dict[str, tuple[float, list["SentimentScore"]]] = {}
_CACHE_TTL_SECONDS = 30 * 60  # 30 minutes

VALID_LABELS = {"very_bullish", "bullish", "neutral", "bearish", "very_bearish"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SentimentScore:
    symbol: str
    headline: str
    score: float          # -1.0 to +1.0
    label: str            # very_bullish | bullish | neutral | bearish | very_bearish
    confidence: float     # 0.0 to 1.0
    summary: str          # one-sentence explanation

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_score_prompt(symbol: str, headlines: list[str]) -> str:
    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
    return f"""You are a financial news sentiment analyst.

SYMBOL: {symbol}

HEADLINES TO SCORE:
{numbered}

For each headline, score its likely short-term price impact on {symbol}.

SCORING RULES:
- score: float from -1.0 (very bearish) to +1.0 (very bullish). 0.0 = neutral.
- label: one of "very_bullish" (+0.6 to +1.0), "bullish" (+0.2 to +0.59),
  "neutral" (-0.19 to +0.19), "bearish" (-0.59 to -0.2), "very_bearish" (-1.0 to -0.6).
- confidence: 0.0 to 1.0 reflecting how clearly the headline signals direction.
- summary: ONE sentence explaining the primary reason for the score.

Respond with ONLY a valid JSON array (no markdown, no extra text):
[
  {{
    "headline": "<exact headline text>",
    "score": <float>,
    "label": "<label>",
    "confidence": <float>,
    "summary": "<one sentence>"
  }},
  ...
]"""


# ---------------------------------------------------------------------------
# Core scoring function
# ---------------------------------------------------------------------------

async def score_headlines(
    symbol: str,
    headlines: list[str],
    claude_client: Any,
) -> list[SentimentScore]:
    """
    Score a list of headlines for the given symbol using a single Claude call.

    Returns a list of SentimentScore — one per headline. Returns an empty
    list if Claude is unavailable or the response cannot be parsed.
    """
    if not headlines:
        return []

    if claude_client is None:
        logger.warning("news_sentiment: claude_client is None, returning empty scores.")
        return []

    prompt = _build_score_prompt(symbol, headlines)
    try:
        response = await asyncio.to_thread(
            claude_client.messages.create,
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        items = json.loads(raw)

        scores: list[SentimentScore] = []
        for item in items:
            raw_score = float(item.get("score", 0.0))
            raw_score = max(-1.0, min(1.0, raw_score))

            raw_label = item.get("label", "neutral")
            label = raw_label if raw_label in VALID_LABELS else "neutral"

            raw_conf = float(item.get("confidence", 0.5))
            raw_conf = max(0.0, min(1.0, raw_conf))

            scores.append(SentimentScore(
                symbol=symbol,
                headline=str(item.get("headline", "")),
                score=raw_score,
                label=label,
                confidence=raw_conf,
                summary=str(item.get("summary", "")),
            ))

        logger.info("news_sentiment: scored %d headlines for %s.", len(scores), symbol)
        return scores

    except json.JSONDecodeError as exc:
        logger.error("news_sentiment: JSON parse error for %s — %s", symbol, exc)
        return []
    except Exception as exc:
        logger.error("news_sentiment: Claude call failed for %s — %s", symbol, exc)
        return []


# ---------------------------------------------------------------------------
# Aggregated sentiment
# ---------------------------------------------------------------------------

async def get_news_sentiment(
    symbol: str,
    news: list[dict[str, Any]],
    claude_client: Any,
) -> dict[str, Any]:
    """
    Extract headlines from news dicts, score them, and return aggregated metrics.

    Results are cached per symbol for 30 minutes.

    Returns dict with keys:
      symbol, scores (list of as_dict()), avg_score, dominant_label,
      bullish_count, bearish_count, sentiment_boost
    """
    now = time.monotonic()
    cached = _sentiment_cache.get(symbol)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        logger.debug("news_sentiment: cache hit for %s", symbol)
        scores = cached[1]
    else:
        headlines = [
            str(item["headline"])
            for item in news
            if isinstance(item, dict) and item.get("headline")
        ]
        scores = await score_headlines(symbol, headlines, claude_client)
        _sentiment_cache[symbol] = (now, scores)

    if not scores:
        return {
            "symbol": symbol,
            "scores": [],
            "avg_score": 0.0,
            "dominant_label": "neutral",
            "bullish_count": 0,
            "bearish_count": 0,
            "sentiment_boost": 0.0,
        }

    avg_score = sum(s.score for s in scores) / len(scores)
    avg_score = round(avg_score, 4)

    bullish_count = sum(1 for s in scores if s.label in {"bullish", "very_bullish"})
    bearish_count = sum(1 for s in scores if s.label in {"bearish", "very_bearish"})

    # Dominant label by frequency; tie goes to neutral
    label_counts: dict[str, int] = {}
    for s in scores:
        label_counts[s.label] = label_counts.get(s.label, 0) + 1
    dominant_label = max(label_counts, key=lambda k: label_counts[k])

    # sentiment_boost: avg_score * 0.08, clamped to [-0.06, +0.06]
    sentiment_boost = round(max(-0.06, min(0.06, avg_score * 0.08)), 4)

    return {
        "symbol": symbol,
        "scores": [s.as_dict() for s in scores],
        "avg_score": avg_score,
        "dominant_label": dominant_label,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "sentiment_boost": sentiment_boost,
    }


# ---------------------------------------------------------------------------
# Prompt injection helper
# ---------------------------------------------------------------------------

def build_news_sentiment_prompt_section(sentiment: dict[str, Any], symbol: str) -> str:
    """
    Return a formatted string block suitable for injecting into a Claude prompt.

    Safe to call with an empty or default sentiment dict.
    """
    avg = sentiment.get("avg_score", 0.0)
    label = sentiment.get("dominant_label", "neutral")
    bullish = sentiment.get("bullish_count", 0)
    bearish = sentiment.get("bearish_count", 0)
    boost = sentiment.get("sentiment_boost", 0.0)
    total = bullish + bearish + sum(
        1 for s in sentiment.get("scores", [])
        if s.get("label") == "neutral"
    )

    lines = [
        f"NEWS SENTIMENT FOR {symbol}:",
        f"  Average score : {avg:+.3f}  ({label})",
        f"  Bullish / Bearish / Total : {bullish} / {bearish} / {total}",
        f"  Confidence boost applied  : {boost:+.4f}",
    ]

    top_scores = sorted(
        sentiment.get("scores", []),
        key=lambda x: abs(x.get("score", 0.0)),
        reverse=True,
    )[:3]

    if top_scores:
        lines.append("  Top headlines:")
        for s in top_scores:
            lines.append(
                f"    [{s.get('score', 0.0):+.2f}] {s.get('headline', '')} "
                f"— {s.get('summary', '')}"
            )

    return "\n".join(lines)


def apply_news_sentiment_adjustment(signal: dict[str, Any], sentiment: dict[str, Any]) -> None:
    """Apply headline sentiment to signal confidence in the trade direction."""
    raw_boost = float(sentiment.get("sentiment_boost", 0.0) or 0.0)
    side = signal.get("signal", "hold")
    if side == "buy":
        directional_boost = raw_boost
    elif side == "sell":
        directional_boost = -raw_boost
    else:
        directional_boost = 0.0

    confidence = float(signal.get("confidence", 0.0) or 0.0)
    signal["confidence"] = min(0.98, max(0.0, confidence + directional_boost))
    signal["news_sentiment_boost"] = directional_boost
    signal["news_sentiment_raw_boost"] = raw_boost
    signal["news_sentiment"] = sentiment
