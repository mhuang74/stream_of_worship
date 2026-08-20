import { cn } from "@/lib/utils";
import { useLocale } from "@/hooks/useLocale";
import type { TranslationKey } from "@/lib/i18n/messages";

interface StatCardProps {
  labelKey: TranslationKey;
  value: number;
  icon: React.ComponentType<{ className?: string }>;
  className?: string;
}

/** Icon-enhanced stat card for the dashboard (2/3/5 responsive grid). */
export function StatCard({ labelKey, value, icon: Icon, className }: StatCardProps) {
  const { t } = useLocale();
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-card p-4 transition-shadow hover:shadow-sm hover:-translate-y-px",
        className
      )}
    >
      <div className="flex items-center gap-3">
        <div className="size-7 rounded-md bg-muted flex items-center justify-center shrink-0">
          <Icon className="size-4 text-muted-foreground" />
        </div>
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground truncate">{t(labelKey)}</p>
          <p className="text-2xl font-bold leading-tight">{value.toLocaleString()}</p>
        </div>
      </div>
    </div>
  );
}
