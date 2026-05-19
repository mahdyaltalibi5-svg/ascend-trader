import { cn } from "@/lib/utils";

interface BadgeProps {
  children: React.ReactNode;
  variant?: "default" | "success" | "danger" | "warning" | "info";
  className?: string;
}

const variants = {
  default: "bg-zinc-800 text-zinc-300",
  success: "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30",
  danger: "bg-red-500/15 text-red-400 border border-red-500/30",
  warning: "bg-amber-500/15 text-amber-400 border border-amber-500/30",
  info: "bg-blue-500/15 text-blue-400 border border-blue-500/30",
};

export function Badge({ children, variant = "default", className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        variants[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
