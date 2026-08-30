import React, { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { formatHash } from '../../services/crypto';
import { clsx } from 'clsx';

interface HashBadgeProps {
  hash: string;
  lead?: number;
  trail?: number;
  label?: string;
  className?: string;
  variant?: 'mono' | 'cyan' | 'emerald' | 'amber' | 'rose' | 'purple' | 'indigo';
}

export const HashBadge: React.FC<HashBadgeProps> = ({
  hash,
  lead = 8,
  trail = 6,
  label,
  className,
  variant = 'mono',
}) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(hash);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const variantStyles = {
    mono: 'bg-slate-900/80 text-slate-300 border-slate-700/60 hover:border-slate-500',
    cyan: 'bg-cyan-950/40 text-cyan-300 border-cyan-700/40 hover:border-cyan-500',
    emerald: 'bg-emerald-950/40 text-emerald-300 border-emerald-700/40 hover:border-emerald-500',
    amber: 'bg-amber-950/40 text-amber-300 border-amber-700/40 hover:border-amber-500',
    rose: 'bg-rose-950/40 text-rose-300 border-rose-700/40 hover:border-rose-500',
    purple: 'bg-purple-950/40 text-purple-300 border-purple-700/40 hover:border-purple-500',
    indigo: 'bg-indigo-950/40 text-indigo-300 border-indigo-700/40 hover:border-indigo-500',
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={`Click to copy full hash: ${hash}`}
      className={clsx(
        'group inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-mono border transition-all cursor-pointer select-none',
        variantStyles[variant],
        className
      )}
    >
      {label && <span className="text-[10px] uppercase font-sans text-slate-400">{label}:</span>}
      <span className="font-mono tracking-tight">{formatHash(hash, lead, trail)}</span>
      {copied ? (
        <Check className="w-3 h-3 text-emerald-400 transition-transform scale-110" />
      ) : (
        <Copy className="w-3 h-3 text-slate-500 group-hover:text-slate-300 transition-opacity opacity-70 group-hover:opacity-100" />
      )}
    </button>
  );
};
