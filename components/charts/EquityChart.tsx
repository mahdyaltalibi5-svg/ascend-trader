"use client";

import { useEffect, useRef } from "react";
import type { IChartApi, UTCTimestamp } from "lightweight-charts";
import { ChartSkeleton } from "@/components/ui/LoadingSkeleton";
import type { PortfolioSnapshot } from "@/types";

interface EquityChartProps {
  snapshots: PortfolioSnapshot[];
  height?: number;
  loading?: boolean;
}

export function EquityChart({ snapshots, height = 280, loading = false }: EquityChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    let chart: IChartApi;

    import("lightweight-charts").then(({ createChart, AreaSeries, ColorType }) => {
      if (!containerRef.current) return;

      chart = createChart(containerRef.current, {
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: "#6b7280",
          fontFamily: "var(--font-inter)",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: "rgba(255,255,255,0.03)" },
          horzLines: { color: "rgba(255,255,255,0.03)" },
        },
        crosshair: {
          mode: 1,
          vertLine: { color: "rgba(0,212,255,0.3)", labelBackgroundColor: "#0f0f17" },
          horzLine: { color: "rgba(0,212,255,0.3)", labelBackgroundColor: "#0f0f17" },
        },
        rightPriceScale: {
          borderColor: "rgba(255,255,255,0.06)",
          textColor: "#6b7280",
        },
        timeScale: {
          borderColor: "rgba(255,255,255,0.06)",
          timeVisible: true,
          secondsVisible: false,
        },
        width: containerRef.current.clientWidth,
        height,
      });

      chartRef.current = chart;

      const series = chart.addSeries(AreaSeries, {
        lineColor: "#00d4ff",
        topColor: "rgba(0,212,255,0.18)",
        bottomColor: "rgba(0,212,255,0.0)",
        lineWidth: 2,
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 5,
        crosshairMarkerBorderColor: "#00d4ff",
        crosshairMarkerBackgroundColor: "#0a0a0f",
        priceLineColor: "rgba(0,212,255,0.3)",
        priceLineStyle: 2,
      });

      if (snapshots.length > 0) {
        const data = snapshots.map((s) => ({
          time: (new Date(s.snapshot_at).getTime() / 1000) as UTCTimestamp,
          value: s.equity,
        }));
        series.setData(data);
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
  }, [snapshots, height]);

  if (loading) return <ChartSkeleton height={height} />;

  return (
    <div className="w-full" ref={containerRef} style={{ height }} />
  );
}
