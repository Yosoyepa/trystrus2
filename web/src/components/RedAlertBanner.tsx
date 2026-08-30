import React from 'react';
import { useApp } from '../context/AppContext';
import { ShieldAlert, RefreshCw, X, AlertOctagon, Terminal } from 'lucide-react';
import { HashBadge } from './common/HashBadge';

export const RedAlertBanner: React.FC = () => {
  const { redAlert, setRedAlert, restoreLedger } = useApp();

  if (!redAlert) return null;

  return (
    <div className="mb-6 rounded-2xl glass-panel-red border-2 border-rose-500/80 p-5 sm:p-6 animate-pulse-fast shadow-2xl shadow-rose-950/80">
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 pb-4 border-b border-rose-500/30">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-xl bg-rose-500/20 border border-rose-500/50 text-rose-400">
            <ShieldAlert className="w-7 h-7" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-0.5 rounded-full text-xs font-bold font-mono bg-rose-500 text-black">
                FAIL-CLOSED ALERT
              </span>
              <h3 className="text-lg font-bold font-display text-rose-200">
                Cryptographic Audit Chain Integrity Compromised!
              </h3>
            </div>
            <p className="text-xs text-rose-300/80 mt-1">
              Tamper detected at block sequence <strong className="text-white">#{redAlert.broken_seq}</strong>.
              All payment routes and gate approvals are automatically locked.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 w-full md:w-auto">
          <button
            onClick={() => restoreLedger()}
            className="flex-1 md:flex-none flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-semibold text-xs transition-all shadow-lg shadow-emerald-950/50 cursor-pointer"
          >
            <RefreshCw className="w-4 h-4" />
            Restore Genesis Integrity
          </button>
          <button
            onClick={() => setRedAlert(null)}
            className="p-2 rounded-xl text-rose-400 hover:text-rose-200 hover:bg-rose-900/40 border border-rose-500/30"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Details Box */}
      <div className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
        <div className="p-3.5 rounded-xl bg-black/50 border border-rose-500/30 space-y-2">
          <div className="flex items-center gap-2 text-rose-300 font-semibold uppercase text-[11px]">
            <AlertOctagon className="w-4 h-4 text-rose-400" />
            <span>Cryptographic Mismatch Details:</span>
          </div>
          <div className="space-y-1.5 text-slate-300">
            <div className="flex justify-between items-center">
              <span className="text-slate-400">Broken Sequence:</span>
              <span className="text-rose-300 font-bold">Seq #{redAlert.broken_seq}</span>
            </div>
            {redAlert.expected_hash && (
              <div className="flex flex-col gap-1">
                <span className="text-slate-400">Recorded / Expected Hash:</span>
                <HashBadge hash={redAlert.expected_hash} variant="emerald" />
              </div>
            )}
            {redAlert.actual_hash && (
              <div className="flex flex-col gap-1">
                <span className="text-slate-400">Corrupted / Computed Hash:</span>
                <HashBadge hash={redAlert.actual_hash} variant="rose" />
              </div>
            )}
          </div>
        </div>

        <div className="p-3.5 rounded-xl bg-black/50 border border-rose-500/30 space-y-2">
          <div className="flex items-center gap-2 text-rose-300 font-semibold uppercase text-[11px]">
            <Terminal className="w-4 h-4 text-rose-400" />
            <span>Auditor Error Output:</span>
          </div>
          <p className="text-rose-200 font-mono text-[11px] leading-relaxed break-words bg-rose-950/40 p-2.5 rounded-lg border border-rose-900/50">
            {redAlert.error}
          </p>
        </div>
      </div>
    </div>
  );
};
