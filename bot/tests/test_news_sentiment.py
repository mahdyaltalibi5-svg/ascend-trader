"""Tests for news_sentiment.py — get_news_sentiment and helpers."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from news_sentiment import (
    get_news_sentiment,
    build_news_sentiment_prompt_section,
    score_headlines,
    SentimentScore,
    _sentiment_cache,
    apply_news_sentiment_adjustment,
)


def run(coro):
    return asyncio.run(coro)


# ── score_headlines ───────────────────────────────────────────────────────────

class TestScoreHeadlines:
    def test_empty_headlines_returns_empty(self):
        result = run(score_headlines("NVDA", [], None))
        assert result == []

    def test_none_client_returns_empty(self):
        result = run(score_headlines("NVDA", ["Big earnings beat!"], None))
        assert result == []

    def test_valid_claude_response_parsed(self):
        import json
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps([{
            "headline": "NVDA beats Q3 estimates by 20%",
            "score": 0.85,
            "label": "very_bullish",
            "confidence": 0.90,
            "summary": "Large earnings beat signals strong demand.",
        }]))]
        mock_client.messages.create = MagicMock(return_value=mock_response)
        result = run(score_headlines("NVDA", ["NVDA beats Q3 estimates by 20%"], mock_client))
        assert len(result) == 1
        assert result[0].score == 0.85
        assert result[0].label == "very_bullish"

    def test_score_clamped_to_range(self):
        import json
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps([{
            "headline": "test",
            "score": 9.99,   # out of range
            "label": "very_bullish",
            "confidence": 0.5,
            "summary": "big move",
        }]))]
        mock_client.messages.create = MagicMock(return_value=mock_response)
        result = run(score_headlines("NVDA", ["test"], mock_client))
        assert result[0].score == 1.0   # clamped

    def test_invalid_label_defaults_to_neutral(self):
        import json
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps([{
            "headline": "test",
            "score": 0.3,
            "label": "super_bullish",   # invalid label
            "confidence": 0.5,
            "summary": "some reason",
        }]))]
        mock_client.messages.create = MagicMock(return_value=mock_response)
        result = run(score_headlines("NVDA", ["test"], mock_client))
        assert result[0].label == "neutral"

    def test_json_parse_error_returns_empty(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not json at all")]
        mock_client.messages.create = MagicMock(return_value=mock_response)
        result = run(score_headlines("NVDA", ["test"], mock_client))
        assert result == []


# ── get_news_sentiment aggregation ────────────────────────────────────────────

class TestGetNewsSentiment:
    def setup_method(self):
        _sentiment_cache.clear()

    def test_no_news_returns_zero_boost(self):
        result = run(get_news_sentiment("AAPL", [], None))
        assert result["sentiment_boost"] == 0.0
        assert result["avg_score"] == 0.0

    def test_boost_clamped_to_0_06(self):
        # Even if avg_score = 1.0, boost should be capped at 0.06
        # We'll mock score_headlines to return very bullish scores
        with patch("news_sentiment.score_headlines", return_value=[
            SentimentScore("AAPL", "big rally", 1.0, "very_bullish", 0.9, "all good"),
            SentimentScore("AAPL", "another rally", 1.0, "very_bullish", 0.9, "strong"),
        ]):
            result = run(get_news_sentiment("AAPL", [{"headline": "x"}], MagicMock()))
        assert result["sentiment_boost"] <= 0.06

    def test_negative_boost_when_bearish(self):
        with patch("news_sentiment.score_headlines", return_value=[
            SentimentScore("AAPL", "crash", -0.9, "very_bearish", 0.9, "bad news"),
        ]):
            result = run(get_news_sentiment("AAPL", [{"headline": "crash"}], MagicMock()))
        assert result["sentiment_boost"] < 0.0
        assert result["sentiment_boost"] >= -0.06

    def test_dominant_label_computed(self):
        with patch("news_sentiment.score_headlines", return_value=[
            SentimentScore("AAPL", "h1", 0.5, "bullish", 0.8, "ok"),
            SentimentScore("AAPL", "h2", 0.6, "bullish", 0.7, "ok"),
            SentimentScore("AAPL", "h3", -0.3, "bearish", 0.7, "ok"),
        ]):
            result = run(get_news_sentiment("AAPL", [{}] * 3, MagicMock()))
        assert result["dominant_label"] == "bullish"
        assert result["bullish_count"] == 2
        assert result["bearish_count"] == 1

    def test_cache_hit_skips_second_call(self):
        with patch("news_sentiment.score_headlines", return_value=[
            SentimentScore("TSLA", "news", 0.4, "bullish", 0.8, "good"),
        ]) as mock_score:
            run(get_news_sentiment("TSLA", [{"headline": "news"}], MagicMock()))
            run(get_news_sentiment("TSLA", [{"headline": "news"}], MagicMock()))
        assert mock_score.call_count == 1  # second call hit cache


class TestSideAwareSentimentAdjustment:
    def test_bullish_news_boosts_buy(self):
        sig = {"signal": "buy", "confidence": 0.70}
        apply_news_sentiment_adjustment(sig, {"sentiment_boost": 0.04})
        assert sig["news_sentiment_boost"] == 0.04
        assert sig["confidence"] == 0.74

    def test_bullish_news_penalizes_sell(self):
        sig = {"signal": "sell", "confidence": 0.70}
        apply_news_sentiment_adjustment(sig, {"sentiment_boost": 0.04})
        assert sig["news_sentiment_boost"] == -0.04
        assert sig["confidence"] == pytest.approx(0.66)

    def test_hold_gets_no_confidence_adjustment(self):
        sig = {"signal": "hold", "confidence": 0.70}
        apply_news_sentiment_adjustment(sig, {"sentiment_boost": 0.04})
        assert sig["news_sentiment_boost"] == 0.0
        assert sig["confidence"] == 0.70


# ── build_news_sentiment_prompt_section ──────────────────────────────────────

class TestBuildPromptSection:
    def test_empty_sentiment_still_renders(self):
        sentiment = {
            "symbol": "NVDA", "scores": [], "avg_score": 0.0,
            "dominant_label": "neutral", "bullish_count": 0,
            "bearish_count": 0, "sentiment_boost": 0.0,
        }
        text = build_news_sentiment_prompt_section(sentiment, "NVDA")
        assert "NVDA" in text
        assert "neutral" in text

    def test_top_headlines_in_output(self):
        sentiment = {
            "symbol": "NVDA",
            "scores": [
                {"headline": "Big earnings beat", "score": 0.9, "label": "very_bullish", "confidence": 0.9, "summary": "strong"},
                {"headline": "Lawsuit filed", "score": -0.7, "label": "bearish", "confidence": 0.8, "summary": "risk"},
            ],
            "avg_score": 0.1,
            "dominant_label": "neutral",
            "bullish_count": 1,
            "bearish_count": 1,
            "sentiment_boost": 0.008,
        }
        text = build_news_sentiment_prompt_section(sentiment, "NVDA")
        assert "Big earnings beat" in text or "Lawsuit" in text
        assert "+0.008" in text or "0.0080" in text
