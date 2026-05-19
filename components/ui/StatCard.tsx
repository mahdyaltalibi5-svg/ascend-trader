"use client";

import { motion } from "framer-motion";
import { TrendingUp, TrendingDown } from "lucide-react";
import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

interface StatCardProps {
  label: string;
  value: string;
  change?: number;
  icon: LucideIcon;
  accentColor?: "cyan" | "success" | "danger" | "purple";
  index?: number;
  loading?: boolean;
}

const ACCENT = {
  cyan:    { bg: "from-cyan/[0.06]",    icon: "text-cyan",    ring: "ring-cyan/15"    },
  success: { bg: "from-success/[0.06]", icon: "text-success", ring: "ring-success/15" },
  danger:  { bg: "from-danger/[0.06]",  icon: "text-danger",  ring: "ring-danger/15"  },
  purple:  { bg: "from-purple/[0.06]",  icon: "text-purple",  ring: "ring-purple/15"  },
};

export function StatCard({
  label,
  value,
  change,
  icon: Icon,
  accentColor = "cyan",
  index = 0,
  loading = false,
}: StatCardProps) {
  const accent = ACCENT[accentColor];
  const isPositive = change !== undefined && change >= 0;

  if (loading) {
    return (
      <div className="glass rounded-2xl p-5">
        <div className="shimmer mb-4 h-3 w-24 rounded" />
        <div className="shimmer h-8 w-32 rounded" />
        <div className="shimmer mt-2 h-3 w-16 rounded" />
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.07, duration: 0.4 }}
      className={cn(
        "glass relative overflow-hidden rounded-2xl p-5",
        `bg-gradient-to-br ${accent.bg} to-transparent`
      )}
    >
      {/* Subtle corner glow */}
      <div className="pointer-events-none absolute -right-4 -top-4 h-16 w-16 rounded-full bg-current opacity-[0.04] blur-xl" />

      <div className="mb-4 flex items-start justify-between">
        <span className="text-xs font-semibold uppercase tracking-widest text-muted">
          {label}
        </span>
        <div className={cn("rounded-lg p-1.5 ring-1", accent.ring, "bg-white/[0.03]")}>
          <Icon className={cn("h-3.5 w-3.5", accent.icon)} />
        </div>
      </div>

      <p className="font-space text-2xl font-bold text-primary">{value}</p>

      {change !== undefined && (
        <div className={cn(
          "mt-2 flex items-center gap-1 text-xs font-medium",
          isPositive ? "text-success" : "text-danger"
        )}>
          {isPositive
            ? <TrendingUp className="h-3 w-3" />
            : <TrendingDown className="h-3 w-3" />
          }
          <span>{isPositive ? "+" : ""}{change.toFixed(2)}%</span>
          <span className="text-muted font-normal">today</span>
        </div>
      )}
    </motion.div>
  );
}
