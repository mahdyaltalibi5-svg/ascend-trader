"""
Ascend Trader — FastAPI trading bot.
Connects to Alpaca for order execution and Supabase for persistence.
Uses Claude (claude-sonnet-4-6) for AI signal generation.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import anthropic
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
supabase: Client = create_client(
    os.environ["NEXT_PUBLIC_SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_HEADERS = {
    "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
    "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
bot_state = {
    "running": False,
    "strategy": "ai_signal",
    "last_signal_at": None,
    "trades_today": 0,
    "win_rate": 0.0,
    "started_at": None,
}

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SignalRequest(BaseModel):
    symbol: str
    timeframe: str = "15Min"

class OrderRequest(BaseModel):
    symbol: str
    qty: float
    side: str  # "buy" | "sell"
    order_type: str = "market"
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None

# ---------------------------------------------------------------------------
# Alpaca helpers
# ---------------------------------------------------------------------------
async def alpaca_get(path: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ALPACA_BASE_URL}{path}", headers=ALPACA_HEADERS)
        r.raise_for_status()
        return r.json()

async def alpaca_post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{ALPACA_BASE_URL}{path}", json=payload, headers=ALPACA_HEADERS
        )
        r.raise_for_status()
        return r.json()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_state["started_at"] = datetime.now(timezone.utc).isoformat()
    yield

app = FastAPI(title="Ascend Trader Bot", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/status")
async def status():
    uptime = 0
    if bot_state["started_at"]:
        started = datetime.fromisoformat(bot_state["started_at"])
        uptime = int((datetime.now(timezone.utc) - started).total_seconds())

    return {
        "running": bot_state["running"],
        "strategy": bot_state["strategy"],
        "last_signal_at": bot_state["last_signal_at"],
        "trades_today": bot_state["trades_today"],
        "win_rate": bot_state["win_rate"],
        "uptime_seconds": uptime,
    }


@app.get("/account")
async def get_account():
    return await alpaca_get("/v2/account")


@app.get("/positions")
async def get_positions():
    return await alpaca_get("/v2/positions")


@app.post("/signal")
async def generate_signal(req: SignalRequest):
    """Generate an AI trade signal using Claude."""
    # Fetch recent bars from Alpaca
    bars = await alpaca_get(
        f"/v2/stocks/{req.symbol}/bars?timeframe={req.timeframe}&limit=50"
    )

    bars_text = "\n".join(
        f"{b['t']} O:{b['o']} H:{b['h']} L:{b['l']} C:{b['c']} V:{b['v']}"
        for b in bars.get("bars", [])
    )

    message = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=(
            "You are a professional quant trader. Analyze the OHLCV bars and return a "
            "JSON signal with: direction (long|short), confidence (0-1), entry_price, "
            "stop_loss, take_profit, and reasoning. Return ONLY valid JSON."
        ),
        messages=[
            {
                "role": "user",
                "content": f"Symbol: {req.symbol}\nTimeframe: {req.timeframe}\n\nBars:\n{bars_text}",
            }
        ],
    )

    import json
    signal_data = json.loads(message.content[0].text)
    signal_data["symbol"] = req.symbol
    signal_data["strategy"] = "ai_signal"
    signal_data["executed"] = False
    signal_data["created_at"] = datetime.now(timezone.utc).isoformat()

    # Persist to Supabase
    supabase.table("signals").insert(signal_data).execute()

    bot_state["last_signal_at"] = signal_data["created_at"]
    return signal_data


@app.post("/order")
async def place_order(req: OrderRequest, background_tasks: BackgroundTasks):
    """Place an order via Alpaca."""
    payload: dict = {
        "symbol": req.symbol,
        "qty": str(req.qty),
        "side": req.side,
        "type": req.order_type,
        "time_in_force": "gtc" if req.order_type != "market" else "day",
    }
    if req.limit_price:
        payload["limit_price"] = str(req.limit_price)

    order = await alpaca_post("/v2/orders", payload)

    # Persist trade to Supabase
    trade_row = {
        "symbol": req.symbol,
        "direction": "long" if req.side == "buy" else "short",
        "status": "pending",
        "order_type": req.order_type,
        "quantity": req.qty,
        "entry_price": req.limit_price,
        "stop_loss": req.stop_loss,
        "take_profit": req.take_profit,
        "alpaca_order_id": order.get("id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    supabase.table("trades").insert(trade_row).execute()

    bot_state["trades_today"] += 1
    return order


@app.delete("/order/{order_id}")
async def cancel_order(order_id: str):
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{ALPACA_BASE_URL}/v2/orders/{order_id}", headers=ALPACA_HEADERS
        )
        if r.status_code not in (200, 204):
            raise HTTPException(status_code=r.status_code, detail=r.text)
    return {"cancelled": order_id}
