import React from 'react';
import { clsx } from 'clsx';

interface ProgressBarProps {
  spent: number;
  reserved?: number;
  total: number;
  currency?: string;
  className?: string;
  showLabels?: boolean;
}

export const ProgressBar: React.FC<ProgressBarProps> = ({
  spent,
  reserved = 0,
  total,
  currency = 'USD',
  className,
  showLabels = true,
}) => {
  const spentPct = Math.min(100, Math.max(0, (spent / total) * 100));
  const reservedPct = Math.min(100 - spentPct, Math.max(0, (reserved / total) * 100));
  const remaining = Math.max(0, total - (spent + reserved));
  const remainingPct = Math.max(0, 100 - (spentPct + reservedPct));

  return (
    <div className={clsx('w-full space-y-1.5', className)}>
      {showLabels && (
        <div className="flex justify-between items-center text-xs">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1 text-slate-300 font-mono">
              <span className="w-2 h-2 rounded-full bg-indigo-500" />
              Spent: <strong className="text-slate-100">${spent.toFixed(2)}</strong>
            </span>
            {reserved > 0 && (
              <span className="flex items-center gap-1 text-amber-400 font-mono">
                <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                Reserved: <strong>${reserved.toFixed(2)}</strong>
              </span>
            )}
          </div>
          <span className="text-slate-400 font-mono">
            Remaining: <strong className="text-emerald-400">${remaining.toFixed(2)}</strong> / ${total.toFixed(2)} {currency}
          </span>
        </div>
      )}

      {/* Bar */}
      <div className="h-3 w-full bg-slate-900/90 rounded-full overflow-hidden border border-slate-800 p-0.5 flex">
        {spentPct > 0 && (
          <div
            style={{ width: `${spentPct}%` }}
            className="h-full bg-gradient-to-r from-indigo-600 to-indigo-400 rounded-l-full transition-all duration-500"
            title={`Spent: $${spent.toFixed(2)} (${spentPct.toFixed(1)}%)`}
          />
        )}
        {reservedPct > 0 && (
          <div
            style={{ width: `${reservedPct}%` }}
            className="h-full bg-amber-400/80 animate-pulse transition-all duration-500"
            title={`Reserved: $${reserved.toFixed(2)} (${reservedPct.toFixed(1)}%)`}
          />
        )}
        {remainingPct > 0 && (
          <div
            style={{ width: `${remainingPct}%` }}
            className="h-full bg-emerald-500/10 rounded-r-full"
          />
        )}
      </div>
    </div>
  );
};
