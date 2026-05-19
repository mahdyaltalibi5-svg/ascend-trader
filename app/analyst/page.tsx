"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { BrainCircuit, Send, Loader2, User } from "lucide-react";
import { cn } from "@/lib/utils";

interface Message {
  role: "user" | "assistant";
  content: string;
}

const SUGGESTED = [
  "What's my biggest risk right now?",
  "Summarize today's performance",
  "Which signals are strongest?",
  "Explain the last trade entry",
];

function MessageBubble({ msg, index }: { msg: Message; index: number }) {
  const isUser = msg.role === "user";
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.03 }}
      className={cn("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}
    >
      {/* Avatar */}
      <div className={cn(
        "flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full",
        isUser
          ? "bg-cyan/10 ring-1 ring-cyan/20"
          : "bg-purple/15 ring-1 ring-purple/25"
      )}>
        {isUser
          ? <User className="h-3.5 w-3.5 text-cyan" />
          : <BrainCircuit className="h-3.5 w-3.5 text-purple" />
        }
      </div>

      {/* Bubble */}
      <div className={cn(
        "max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-relaxed",
        isUser
          ? "rounded-tr-sm bg-cyan/10 text-primary ring-1 ring-cyan/15"
          : "rounded-tl-sm bg-surface text-primary ring-1 ring-white/[0.06]"
      )}>
        {msg.content || (
          <span className="flex items-center gap-2 text-muted">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            <span>Thinking...</span>
          </span>
        )}
      </div>
    </motion.div>
  );
}

export default function AnalystPage() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content:
        "Hey — I'm your AI trading analyst. I have context on your current portfolio, recent trades, and active signals. What do you want to know?",
    },
  ]);
  const [input, setInput]     = useState("");
  const [streaming, setStreaming] = useState(false);
  const endRef   = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = useCallback(async (content: string) => {
    if (!content.trim() || streaming) return;

    const userMsg: Message = { role: "user", content: content.trim() };
    const next = [...messages, userMsg];
    setMessages([...next, { role: "assistant", content: "" }]);
    setInput("");
    setStreaming(true);

    try {
      const res = await fetch("/api/analyst", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: next.map((m) => ({ role: m.role, content: m.content })),
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: "Request failed" }));
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = {
            role: "assistant",
            content: err.error ?? "Something went wrong. Check your Anthropic API key.",
          };
          return updated;
        });
        return;
      }

      const reader  = res.body?.getReader();
      const decoder = new TextDecoder();
      let full = "";

      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        full += decoder.decode(value, { stream: true });
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = { role: "assistant", content: full };
          return updated;
        });
      }
    } catch {
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          role: "assistant",
          content: "Connection error. Make sure the app is running and your API key is configured.",
        };
        return updated;
      });
    } finally {
      setStreaming(false);
      inputRef.current?.focus();
    }
  }, [messages, streaming]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    send(input);
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-white/[0.06] px-6 py-4">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-purple/15 ring-1 ring-purple/25">
          <BrainCircuit className="h-4.5 w-4.5 text-purple" />
        </div>
        <div>
          <h1 className="font-space text-lg font-semibold text-primary">AI Analyst</h1>
          <p className="text-xs text-muted">Powered by Claude — portfolio context auto-loaded</p>
        </div>
        <div className="ml-auto flex items-center gap-1.5 rounded-lg border border-purple/20 bg-purple/[0.06] px-3 py-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-purple animate-pulse-slow" />
          <span className="text-xs font-medium text-purple">Claude</span>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl space-y-5">
          <AnimatePresence>
            {messages.map((msg, i) => (
              <MessageBubble key={i} msg={msg} index={i} />
            ))}
          </AnimatePresence>
          <div ref={endRef} />
        </div>
      </div>

      {/* Suggested chips */}
      {messages.length <= 1 && (
        <div className="border-t border-white/[0.06] px-6 pt-4 pb-2">
          <div className="mx-auto max-w-3xl">
            <p className="mb-2.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
              Suggested
            </p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTED.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="rounded-xl border border-white/[0.08] bg-surface px-3 py-1.5 text-xs text-muted transition-colors hover:border-cyan/30 hover:text-primary"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Input */}
      <div className="border-t border-white/[0.06] px-6 py-4">
        <form onSubmit={handleSubmit} className="mx-auto flex max-w-3xl items-center gap-3">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={streaming}
            placeholder="Ask about your portfolio, trades, or signals..."
            className="flex-1 rounded-xl border border-white/[0.08] bg-surface px-4 py-2.5 text-sm text-primary placeholder-muted outline-none transition-all focus:border-cyan/40 focus:ring-1 focus:ring-cyan/20 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!input.trim() || streaming}
            className={cn(
              "flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl transition-all",
              input.trim() && !streaming
                ? "bg-cyan text-base hover:bg-cyan/90 glow-cyan"
                : "bg-surface text-muted cursor-not-allowed"
            )}
          >
            {streaming
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Send className="h-4 w-4" />
            }
          </button>
        </form>
      </div>
    </div>
  );
}
