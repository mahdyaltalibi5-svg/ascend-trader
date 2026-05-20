"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  LayoutDashboard,
  ArrowLeftRight,
  Zap,
  Beaker,
  BrainCircuit,
  Settings,
  ChevronRight,
  Activity,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useBotStatus } from "@/hooks/useBotStatus";

const NAV = [
  { href: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { href: "/trades",    icon: ArrowLeftRight,  label: "Trades"    },
  { href: "/signals",   icon: Zap,             label: "Signals"   },
  { href: "/backtest",  icon: Beaker,          label: "Research"  },
  { href: "/analyst",   icon: BrainCircuit,    label: "Analyst"   },
  { href: "/settings",  icon: Settings,        label: "Settings"  },
];

const STATUS_COLOR = {
  running: "bg-success",
  stopped: "bg-danger",
  error:   "bg-amber-400",
} as const;

const STATUS_LABEL = {
  running: "Bot running",
  stopped: "Bot offline",
  error:   "Bot error",
} as const;

export function AppShell({ children }: { children: React.ReactNode }) {
  const [expanded, setExpanded] = useState(true);
  const pathname = usePathname();
  const { status, lastScan } = useBotStatus();

  // Collapse on mobile by default
  useEffect(() => {
    if (window.innerWidth < 768) setExpanded(false);
  }, []);

  return (
    <div className="grid-bg flex h-screen overflow-hidden">
      {/* Sidebar */}
      <motion.aside
        animate={{ width: expanded ? 220 : 64 }}
        transition={{ type: "spring", stiffness: 320, damping: 32 }}
        className="relative z-20 flex h-full flex-shrink-0 flex-col overflow-hidden border-r border-white/[0.06] bg-base"
      >
        {/* Top — Logo */}
        <div className="flex h-16 items-center border-b border-white/[0.06] px-4">
          <Link href="/dashboard" className="flex items-center gap-2.5 overflow-hidden">
            <div className="relative flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-cyan/10">
              <Activity className="h-4 w-4 text-cyan" />
              <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-cyan animate-pulse-slow" />
            </div>
            <AnimatePresence>
              {expanded && (
                <motion.span
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -8 }}
                  transition={{ duration: 0.15 }}
                  className="font-space text-sm font-bold tracking-[0.15em] text-primary whitespace-nowrap"
                >
                  ASCEND
                  <span className="text-cyan">.</span>
                </motion.span>
              )}
            </AnimatePresence>
          </Link>
        </div>

        {/* Nav */}
        <nav className="flex flex-1 flex-col gap-1 p-2 pt-4">
          {NAV.map(({ href, icon: Icon, label }) => {
            const active = pathname.startsWith(href);
            return (
              <Link key={href} href={href}>
                <motion.div
                  whileHover={{ x: 2 }}
                  whileTap={{ scale: 0.97 }}
                  className={cn(
                    "relative flex h-10 items-center gap-3 rounded-xl px-3 text-sm font-medium transition-colors",
                    active
                      ? "bg-cyan/10 text-cyan"
                      : "text-muted hover:bg-white/[0.04] hover:text-primary"
                  )}
                >
                  {active && (
                    <motion.div
                      layoutId="nav-active"
                      className="absolute inset-0 rounded-xl bg-cyan/[0.08] ring-1 ring-cyan/20"
                      transition={{ type: "spring", stiffness: 380, damping: 34 }}
                    />
                  )}
                  <Icon className="relative h-4 w-4 flex-shrink-0" />
                  <AnimatePresence>
                    {expanded && (
                      <motion.span
                        initial={{ opacity: 0, x: -4 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -4 }}
                        transition={{ duration: 0.12 }}
                        className="relative whitespace-nowrap"
                      >
                        {label}
                      </motion.span>
                    )}
                  </AnimatePresence>
                </motion.div>
              </Link>
            );
          })}
        </nav>

        {/* Bottom — Bot Status */}
        <div className="border-t border-white/[0.06] p-3">
          <div className={cn(
            "flex items-center gap-3 rounded-xl p-2.5",
            expanded ? "bg-surface" : ""
          )}>
            <div className="relative flex-shrink-0">
              <span className={cn(
                "block h-2.5 w-2.5 rounded-full",
                STATUS_COLOR[status]
              )} />
              {status === "running" && (
                <span className={cn(
                  "absolute inset-0 rounded-full animate-ping opacity-75",
                  STATUS_COLOR[status]
                )} />
              )}
            </div>
            <AnimatePresence>
              {expanded && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.12 }}
                  className="min-w-0"
                >
                  <p className="text-xs font-medium text-primary">{STATUS_LABEL[status]}</p>
                  {lastScan && (
                    <p className="truncate text-[10px] text-muted">
                      Last scan {lastScan}
                    </p>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>

        {/* Collapse toggle */}
        <button
          onClick={() => setExpanded((e) => !e)}
          className="absolute -right-3 top-20 z-30 flex h-6 w-6 items-center justify-center rounded-full border border-white/[0.1] bg-surface text-muted hover:text-primary transition-colors"
        >
          <motion.div animate={{ rotate: expanded ? 180 : 0 }} transition={{ duration: 0.2 }}>
            <ChevronRight className="h-3.5 w-3.5" />
          </motion.div>
        </button>
      </motion.aside>

      {/* Main content */}
      <main className="flex flex-1 flex-col overflow-hidden">
        {children}
      </main>
    </div>
  );
}
