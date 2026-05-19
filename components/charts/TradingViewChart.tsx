"use client";

import { useEffect, useRef } from "react";
import { ChartSkeleton } from "@/components/ui/LoadingSkeleton";
import { cn } from "@/lib/utils";

interface TradingViewChartProps {
  symbol: string;
  interval?: "1" | "5" | "15" | "30" | "60" | "240" | "D" | "W";
  height?: number;
  className?: string;
  showToolbar?: boolean;
  allowSymbolChange?: boolean;
}

// Map plain ticker → TradingView exchange:symbol format
function resolveSymbol(symbol: string): string {
  if (symbol.includes(":")) return symbol;
  const etfs = ["SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "XLF"];
  if (etfs.includes(symbol.toUpperCase())) return `AMEX:${symbol.toUpperCase()}`;
  return `NASDAQ:${symbol.toUpperCase()}`;
}

declare global {
  interface Window {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    TradingView: any;
  }
}

let scriptLoaded = false;
let scriptLoading = false;
const callbacks: (() => void)[] = [];

function loadTVScript(cb: () => void) {
  if (scriptLoaded) { cb(); return; }
  callbacks.push(cb);
  if (scriptLoading) return;
  scriptLoading = true;
  const s = document.createElement("script");
  s.src = "https://s3.tradingview.com/tv.js";
  s.async = true;
  s.onload = () => {
    scriptLoaded = true;
    callbacks.forEach((fn) => fn());
    callbacks.length = 0;
  };
  document.head.appendChild(s);
}

let widgetSeq = 0;

export function TradingViewChart({
  symbol,
  interval = "15",
  height = 420,
  className,
  showToolbar = true,
  allowSymbolChange = false,
}: TradingViewChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetRef    = useRef<unknown>(null);
  const containerId  = useRef(`tv_widget_${++widgetSeq}`);

  useEffect(() => {
    let destroyed = false;

    loadTVScript(() => {
      if (destroyed || !containerRef.current) return;

      try {
        widgetRef.current = new window.TradingView.widget({
          container_id:        containerId.current,
          autosize:            true,
          symbol:              resolveSymbol(symbol),
          interval,
          timezone:            "Etc/UTC",
          theme:               "dark",
          style:               "1",          // candlesticks
          locale:              "en",
          backgroundColor:     "#0f0f17",
          gridColor:           "rgba(255,255,255,0.03)",
          toolbar_bg:          "#0f0f17",
          hide_top_toolbar:    !showToolbar,
          hide_legend:         false,
          hide_side_toolbar:   false,
          allow_symbol_change: allowSymbolChange,
          enable_publishing:   false,
          withdateranges:      true,
          save_image:          false,
          studies:             ["RSI@tv-basicstudies", "MASimple@tv-basicstudies"],
          overrides: {
            "paneProperties.background":            "#0f0f17",
            "paneProperties.backgroundGradientStartColor": "#0f0f17",
            "paneProperties.backgroundGradientEndColor":   "#0f0f17",
            "paneProperties.vertGridProperties.color":     "rgba(255,255,255,0.03)",
            "paneProperties.horzGridProperties.color":     "rgba(255,255,255,0.03)",
            "symbolWatermarkProperties.transparency":       100,
            "scalesProperties.textColor":           "#6b7280",
            "mainSeriesProperties.candleStyle.upColor":         "#00ff88",
            "mainSeriesProperties.candleStyle.downColor":       "#ff3b5c",
            "mainSeriesProperties.candleStyle.borderUpColor":   "#00ff88",
            "mainSeriesProperties.candleStyle.borderDownColor": "#ff3b5c",
            "mainSeriesProperties.candleStyle.wickUpColor":     "#00ff88",
            "mainSeriesProperties.candleStyle.wickDownColor":   "#ff3b5c",
          },
          studies_overrides: {
            "RSI.RSI.color":       "#00d4ff",
            "RSI.RSI.linewidth":   1.5,
            "Volume.volume.color.0": "rgba(255,59,92,0.4)",
            "Volume.volume.color.1": "rgba(0,255,136,0.4)",
          },
        });
      } catch {
        // widget init failed (e.g. container gone before script loaded)
      }
    });

    return () => {
      destroyed = true;
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (widgetRef.current as any)?.remove?.();
      } catch {}
      widgetRef.current = null;
    };
  }, [symbol, interval, showToolbar, allowSymbolChange]);

  return (
    <div className={cn("w-full overflow-hidden rounded-xl", className)} style={{ height }}>
      <div id={containerId.current} ref={containerRef} className="h-full w-full" />
    </div>
  );
}
