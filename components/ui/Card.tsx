import { cn } from "@/lib/utils";

interface CardProps {
  children: React.ReactNode;
  className?: string;
  glass?: boolean;
}

export function Card({ children, className, glass }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-2xl border border-white/5 p-6",
        glass
          ? "bg-white/5 backdrop-blur-md"
          : "bg-[#111122]",
        className
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("mb-4 flex items-center justify-between", className)}>
      {children}
    </div>
  );
}

export function CardTitle({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <h3 className={cn("text-sm font-medium text-zinc-400 uppercase tracking-wider", className)}>
      {children}
    </h3>
  );
}
