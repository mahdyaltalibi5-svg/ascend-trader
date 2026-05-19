"use client";

import { useEffect, useRef } from "react";
import type { IChartApi, UTCTimestamp } from "lightweight-charts";
import { ChartSkeleton } from "@/components/ui/LoadingSkeleton";
import type { Candle } from "@/types";

interface CandleChartProps {
  candles: Candle[];
  entryPrice?: number;
  entryTime?: number;
  exitPrice?: number;
  exitTime?: number;
  height?: number;
  loading?: boolean;
}

export function CandleChart({
  candles,
  entryPrice,
  entryTime,
  exitPrice,
  exitTime,
  height = 260,
  loading = false,
}: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    import("lightweight-charts").then(({
      createChart,
      CandlestickSeries,
      ColorType,
    }) => {
      if (!containerRef.current) return;

      const chart = createChart(containerRef.current, {
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: "#6b7280",
          fontFamily: "var(--font-inter)",
          fontSize: 10,
        },
        grid: {
          vertLines: { color: "rgba(255,255,255,0.03)" },
          horzLines: { color: "rgba(255,255,255,0.03)" },
        },
        crosshair: {
          vertLine: { color: "rgba(0,212,255,0.25)", labelBackgroundColor: "#0f0f17" },
          horzLine: { color: "rgba(0,212,255,0.25)", labelBackgroundColor: "#0f0f17" },
        },
        rightPriceScale: { borderColor: "rgba(255,255,255,0.06)" },
        timeScale: {
          borderColor: "rgba(255,255,255,0.06)",
          timeVisible: true,
        },
        width: containerRef.current.clientWidth,
        height,
      });

      chartRef.current = chart;

      const series = chart.addSeries(CandlestickSeries, {
        upColor:         "#00ff88",
        downColor:       "#ff3b5c",
        borderUpColor:   "#00ff88",
        borderDownColor: "#ff3b5c",
        wickUpColor:     "#00ff88",
        wickDownColor:   "#ff3b5c",
      });

      if (candles.length > 0) {
        series.setData(
          candles.map((c) => ({
            time:  c.time as UTCTimestamp,
            open:  c.open,
            high:  c.high,
            low:   c.low,
            close: c.close,
          }))
        );

        // Entry / exit markers
        const markers: object[] = [];
        if (entryTime && entryPrice) {
          markers.push({
            time:     entryTime as UTCTimestamp,
            position: "belowBar",
            color:    "#00ff88",
            shape:    "arrowUp",
            text:     `Entry $${entryPrice.toFixed(2)}`,
            size:     1,
          });
        }
        if (exitTime && exitPrice) {
          markers.push({
            time:     exitTime as UTCTimestamp,
            position: "aboveBar",
            color:    "#ff3b5c",
            shape:    "arrowDown",
            text:     `Exit $${exitPrice.toFixed(2)}`,
            size:     1,
          });
        }
        if (markers.length) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (series as any).setMarkers(markers);
        }

        chart.timeScale().fitContent();
      }

      const observer = new ResizeObserver(() => {
        if (containerRef.current) {
          chart.applyOptions({ width: containerRef.current.clientWidth });
        }
      });
      observer.observe(containerRef.current);

      return () => observer.disconnect();
    });

    return () => {
      chartRef.current?.remove();
      chartRef.current = null;
    };
  }, [candles, entryPrice, entryTime, exitPrice, exitTime, height]);

  if (loading) return <ChartSkeleton height={height} />;

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}
