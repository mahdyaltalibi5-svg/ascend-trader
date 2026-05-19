import { NextRequest } from "next/server";
import { createServiceClient } from "@/lib/supabase/server";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  if (!process.env.ANTHROPIC_API_KEY) {
    return Response.json(
      { error: "Anthropic API key not configured. Add ANTHROPIC_API_KEY to .env.local." },
      { status: 503 }
    );
  }

  const { messages } = await req.json();

  // Fetch portfolio context server-side (service role — never exposed to client)
  const supabase = await createServiceClient();

  const [portfolioRes, tradesRes, signalsRes] = await Promise.all([
    supabase.from("portfolio").select("*").limit(1).single(),
    supabase.from("trades").select("symbol,side,pnl,status,entry_price,exit_price").order("created_at", { ascending: false }).limit(10),
    supabase.from("signals").select("symbol,signal,strength,strategy,created_at").order("created_at", { ascending: false }).limit(5),
  ]);

  const contextBlock = `
CURRENT PORTFOLIO:
${JSON.stringify(portfolioRes.data ?? {}, null, 2)}

RECENT TRADES (last 10):
${JSON.stringify(tradesRes.data ?? [], null, 2)}

ACTIVE SIGNALS (last 5):
${JSON.stringify(signalsRes.data ?? [], null, 2)}
`.trim();

  const system = `You are an expert AI trading analyst for the Ascend Trader platform.
You have access to the user's live portfolio, trade history, and recent AI signals.
Be concise, data-driven, and direct. Use numbers when available. Flag risks clearly.
Never give generic financial advice — always ground your answers in the user's actual data.

${contextBlock}`;

  // Dynamic import to avoid issues when key is not set in dev
  const Anthropic = (await import("@anthropic-ai/sdk")).default;
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  const stream = await client.messages.stream({
    model: "claude-sonnet-4-6",
    max_tokens: 1024,
    system,
    messages,
  });

  const readable = new ReadableStream({
    async start(controller) {
      try {
        for await (const chunk of stream) {
          if (
            chunk.type === "content_block_delta" &&
            chunk.delta.type === "text_delta"
          ) {
            controller.enqueue(new TextEncoder().encode(chunk.delta.text));
          }
        }
      } finally {
        controller.close();
      }
    },
  });

  return new Response(readable, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}
