"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

interface SignalStrengthBarProps {
  strength: number;
  signal?: "BUY" | "SELL" | "HOLD" | string;
  showLabel?: boolean;
}

export function SignalStrengthBar({
  strength,
  signal = "HOLD",
  showLabel = true,
}: SignalStrengthBarProps) {
  const pct = Math.min(Math.max(strength, 0), 1) * 100;
  const sig = signal.toUpperCase();

  const color =
    sig === "BUY"  ? "bg-cyan"    :
    sig === "SELL" ? "bg-danger"  :
                     "bg-muted";

  const label =
    pct >= 80 ? "Very Strong" :
    pct >= 60 ? "Strong"      :
    pct >= 40 ? "Moderate"    :
    pct >= 20 ? "Weak"        :
                "Very Weak";

  return (
    <div className="w-full">
      {showLabel && (
        <div className="mb-1.5 flex items-center justify-between text-[10px] text-muted">
          <span>Strength</span>
          <span className="font-medium text-primary">{label}</span>
        </div>
      )}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.8, ease: "easeOut", delay: 0.1 }}
          className={cn("h-full rounded-full", color)}
        />
      </div>
    </div>
  );
}
