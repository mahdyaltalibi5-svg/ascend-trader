"use client";

import { useTheme } from "next-themes";
import { Sun, Moon, Bell } from "lucide-react";
import { motion } from "framer-motion";

interface TopBarProps {
  title: string;
}

export function TopBar({ title }: TopBarProps) {
  const { theme, setTheme } = useTheme();

  return (
    <header className="flex h-14 items-center justify-between border-b border-white/5 bg-[#0D0D1A]/80 px-6 backdrop-blur-sm">
      <h1 className="text-sm font-semibold text-white">{title}</h1>
      <div className="flex items-center gap-2">
        <motion.button
          whileTap={{ scale: 0.9 }}
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          className="rounded-lg p-2 text-zinc-500 hover:bg-white/5 hover:text-zinc-200 transition-colors"
        >
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </motion.button>
        <motion.button
          whileTap={{ scale: 0.9 }}
          className="rounded-lg p-2 text-zinc-500 hover:bg-white/5 hover:text-zinc-200 transition-colors"
        >
          <Bell className="h-4 w-4" />
        </motion.button>
      </div>
    </header>
  );
}
