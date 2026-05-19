import { cn } from "@/lib/utils";

interface BotStatusBadgeProps {
  status: "running" | "stopped" | "error";
  showLabel?: boolean;
}

const CONFIG = {
  running: { dot: "bg-success", label: "Running", text: "text-success" },
  stopped: { dot: "bg-muted",   label: "Offline",  text: "text-muted"   },
  error:   { dot: "bg-danger",  label: "Error",    text: "text-danger"  },
};

export function BotStatusBadge({ status, showLabel = true }: BotStatusBadgeProps) {
  const cfg = CONFIG[status];

  return (
    <div className="flex items-center gap-2">
      <span className="relative flex h-2.5 w-2.5 flex-shrink-0">
        <span className={cn(
          "absolute inline-flex h-full w-full rounded-full opacity-75",
          status === "running" ? "animate-ping bg-success" : ""
        )} />
        <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", cfg.dot)} />
      </span>
      {showLabel && (
        <span className={cn("text-xs font-medium", cfg.text)}>{cfg.label}</span>
      )}
    </div>
  );
}
