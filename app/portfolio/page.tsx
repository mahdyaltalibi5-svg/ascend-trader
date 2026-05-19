"use client";

import { TopBar } from "@/components/layout/TopBar";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { usePortfolio } from "@/hooks/usePortfolio";
import { formatCurrency, formatPercent, pnlColor } from "@/lib/utils";

export default function PortfolioPage() {
  const { portfolio, loading } = usePortfolio();

  return (
    <div className="flex flex-col h-full">
      <TopBar title="Portfolio" />
      <div className="flex-1 p-6 space-y-6">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
          {[
            { label: "Equity", value: portfolio ? formatCurrency(portfolio.equity) : "—" },
            { label: "Cash", value: portfolio ? formatCurrency(portfolio.cash) : "—" },
            { label: "Buying Power", value: portfolio ? formatCurrency(portfolio.buying_power) : "—" },
            { label: "Day P&L", value: portfolio ? formatCurrency(portfolio.day_pnl) : "—", color: portfolio ? pnlColor(portfolio.day_pnl) : "" },
            { label: "Total P&L", value: portfolio ? formatCurrency(portfolio.total_pnl) : "—", color: portfolio ? pnlColor(portfolio.total_pnl) : "" },
            { label: "Positions", value: portfolio ? String(portfolio.positions?.length ?? 0) : "—" },
          ].map((stat) => (
            <Card key={stat.label}>
              <CardHeader>
                <CardTitle>{stat.label}</CardTitle>
              </CardHeader>
              <p className={`text-xl font-bold ${stat.color ?? "text-white"}`}>{stat.value}</p>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
