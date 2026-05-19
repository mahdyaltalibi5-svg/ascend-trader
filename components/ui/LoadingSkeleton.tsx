import { cn } from "@/lib/utils";

interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className }: SkeletonProps) {
  return (
    <div className={cn("shimmer rounded-lg", className)} />
  );
}

export function StatCardSkeleton() {
  return (
    <div className="glass rounded-2xl p-5">
      <div className="mb-4 flex items-start justify-between">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-7 w-7 rounded-lg" />
      </div>
      <Skeleton className="h-8 w-28" />
      <Skeleton className="mt-2 h-3 w-16" />
    </div>
  );
}

export function TradeRowSkeleton() {
  return (
    <div className="flex items-center gap-4 border-b border-white/[0.04] px-4 py-3.5">
      <Skeleton className="h-4 w-14" />
      <Skeleton className="h-5 w-12 rounded" />
      <Skeleton className="h-4 w-20" />
      <Skeleton className="h-4 w-20" />
      <Skeleton className="h-4 w-10" />
      <Skeleton className="h-4 w-16 ml-auto" />
    </div>
  );
}

export function SignalCardSkeleton() {
  return (
    <div className="glass rounded-2xl p-5">
      <div className="mb-3 flex items-center justify-between">
        <Skeleton className="h-7 w-16" />
        <Skeleton className="h-6 w-14 rounded" />
      </div>
      <Skeleton className="mb-3 h-3 w-24" />
      <Skeleton className="h-1.5 w-full rounded-full" />
      <div className="mt-3 grid grid-cols-3 gap-2">
        <Skeleton className="h-8 rounded-lg" />
        <Skeleton className="h-8 rounded-lg" />
        <Skeleton className="h-8 rounded-lg" />
      </div>
    </div>
  );
}

export function ChartSkeleton({ height = 280 }: { height?: number }) {
  return (
    <div className="w-full shimmer rounded-xl" style={{ height }} />
  );
}
