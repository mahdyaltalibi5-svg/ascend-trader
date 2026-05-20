"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import {
  LayoutDashboard,
  ArrowLeftRight,
  Zap,
  PieChart,
  Beaker,
  Settings,
  TrendingUp,
} from "lucide-react";
import { cn } from "@/lib/utils";

const nav = [
  { href: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { href: "/trades", icon: ArrowLeftRight, label: "Trades" },
  { href: "/signals", icon: Zap, label: "Signals" },
  { href: "/backtest", icon: Beaker, label: "Research" },
  { href: "/portfolio", icon: PieChart, label: "Portfolio" },
  { href: "/settings", icon: Settings, label: "Settings" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex h-screen w-16 flex-col items-center border-r border-white/5 bg-[#0D0D1A] py-6 lg:w-56 lg:items-start lg:px-4">
      <div className="mb-8 flex items-center gap-2 px-2">
        <TrendingUp className="h-6 w-6 text-violet-500" />
        <span className="hidden text-sm font-bold tracking-tight text-white lg:block">
          Ascend Trader
        </span>
      </div>

      <nav className="flex w-full flex-col gap-1">
        {nav.map(({ href, icon: Icon, label }) => {
          const active = pathname.startsWith(href);
          return (
            <Link key={href} href={href}>
              <motion.div
                whileHover={{ x: 2 }}
                className={cn(
                  "relative flex items-center gap-3 rounded-xl px-2 py-2.5 text-sm font-medium transition-colors",
                  active
                    ? "bg-violet-500/15 text-violet-400"
                    : "text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
                )}
              >
                {active && (
                  <motion.div
                    layoutId="sidebar-active"
                    className="absolute inset-0 rounded-xl bg-violet-500/10"
                  />
                )}
                <Icon className="h-4 w-4 shrink-0" />
                <span className="hidden lg:block">{label}</span>
              </motion.div>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
