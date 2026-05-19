import { cn } from "@/lib/utils";

type Signal = "BUY" | "SELL" | "HOLD" | "buy" | "sell" | "hold";

interface TradeBadgeProps {
  signal: Signal;
  size?: "sm" | "md";
  glow?: boolean;
}

const CONFIG = {
  BUY:  { label: "BUY",  cls: "bg-cyan/10 text-cyan border-cyan/25",    glow: "glow-cyan"  },
  SELL: { label: "SELL", cls: "bg-danger/10 text-danger border-danger/25", glow: "glow-red"   },
  HOLD: { label: "HOLD", cls: "bg-white/5 text-muted border-white/10",   glow: ""           },
};

export function TradeBadge({ signal, size = "sm", glow = false }: TradeBadgeProps) {
  const key = signal.toUpperCase() as keyof typeof CONFIG;
  const cfg = CONFIG[key] ?? CONFIG.HOLD;

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border font-space font-semibold tracking-wider",
        cfg.cls,
        glow && cfg.glow,
        size === "sm" ? "px-2 py-0.5 text-[10px]" : "px-3 py-1 text-xs"
      )}
    >
      {cfg.label}
    </span>
  );
}
