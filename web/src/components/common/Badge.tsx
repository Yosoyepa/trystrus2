import React from 'react';
import { clsx } from 'clsx';

interface BadgeProps {
  variant?: 'emerald' | 'indigo' | 'rose' | 'amber' | 'cyan' | 'purple' | 'slate';
  size?: 'sm' | 'md' | 'lg';
  children: React.ReactNode;
  pulse?: boolean;
  className?: string;
}

export const Badge: React.FC<BadgeProps> = ({
  variant = 'indigo',
  size = 'md',
  children,
  pulse = false,
  className,
}) => {
  const variantStyles = {
    emerald: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
    indigo: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/30',
    rose: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
    amber: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
    cyan: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
    purple: 'bg-purple-500/10 text-purple-400 border-purple-500/30',
    slate: 'bg-slate-500/10 text-slate-400 border-slate-500/30',
  };

  const sizeStyles = {
    sm: 'text-[11px] px-2 py-0.5 font-medium',
    md: 'text-xs px-2.5 py-1 font-semibold',
    lg: 'text-sm px-3 py-1.5 font-bold',
  };

  const dotColors = {
    emerald: 'bg-emerald-400',
    indigo: 'bg-indigo-400',
    rose: 'bg-rose-400',
    amber: 'bg-amber-400',
    cyan: 'bg-cyan-400',
    purple: 'bg-purple-400',
    slate: 'bg-slate-400',
  };

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full border tracking-wide uppercase font-mono shadow-sm',
        variantStyles[variant],
        sizeStyles[size],
        className
      )}
    >
      {pulse && (
        <span className="relative flex h-2 w-2">
          <span
            className={clsx(
              'animate-ping absolute inline-flex h-full w-full rounded-full opacity-75',
              dotColors[variant]
            )}
          />
          <span className={clsx('relative inline-flex rounded-full h-2 w-2', dotColors[variant])} />
        </span>
      )}
      {children}
    </span>
  );
};
