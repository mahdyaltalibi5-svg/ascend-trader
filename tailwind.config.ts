import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        base:        "#0a0a0f",
        surface:     "#0f0f17",
        "surface-2": "#141420",
        border:      "rgba(255,255,255,0.06)",
        cyan:        "#00d4ff",
        purple:      "#7c3aed",
        success:     "#00ff88",
        danger:      "#ff3b5c",
        primary:     "#f0f0f0",
        muted:       "#6b7280",
      },
      fontFamily: {
        space: ["var(--font-space)", "sans-serif"],
        sans:  ["var(--font-inter)", "sans-serif"],
      },
      keyframes: {
        shimmer: {
          "0%":   { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "calc(400px + 100%) 0" },
        },
        "pulse-slow": {
          "0%, 100%": { opacity: "1" },
          "50%":      { opacity: "0.4" },
        },
        "glow-cyan": {
          "0%, 100%": { boxShadow: "0 0 8px rgba(0,212,255,0.3)" },
          "50%":      { boxShadow: "0 0 24px rgba(0,212,255,0.7)" },
        },
      },
      animation: {
        shimmer:      "shimmer 1.6s infinite linear",
        "pulse-slow": "pulse-slow 2.5s ease-in-out infinite",
        "glow-cyan":  "glow-cyan 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
export default config;
