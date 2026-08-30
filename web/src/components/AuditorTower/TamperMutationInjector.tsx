import React, { useState } from 'react';
import { useApp } from '../../context/AppContext';
import { Bug, RefreshCw, Zap, WifiOff } from 'lucide-react';
import { Badge } from '../common/Badge';

export const TamperMutationInjector: React.FC = () => {
  const { auditEvents, tamperBlock, verifyChain, restoreLedger, auditBackendError } = useApp();
  const [selectedSeq, setSelectedSeq] = useState<number>(4);
  const [tamperField, setTamperField] = useState<'price' | 'amount' | 'limits' | 'custom'>('price');
  const [customValue, setCustomValue] = useState<string>('999.00');

  const targetEvent = auditEvents.find((e) => e.seq === selectedSeq) || auditEvents[0];

  const handleInject = async () => {
    if (!targetEvent) return;

    let mutatedPayload: Record<string, unknown> = { ...targetEvent.payload };

    if (tamperField === 'price') {
      mutatedPayload = { ...mutatedPayload, price: parseFloat(customValue) || 999.0, tampered: true };
    } else if (tamperField === 'amount') {
      mutatedPayload = { ...mutatedPayload, amount: customValue, tampered: true };
    } else if (tamperField === 'limits') {
      mutatedPayload = {
        ...mutatedPayload,
        limits: { max_per_txn: 9999, total_budget: 9999, max_txn: 99 },
        tampered: true,
      };
    } else {
      try {
        mutatedPayload = JSON.parse(customValue);
      } catch {
        mutatedPayload = { ...mutatedPayload, corrupted_byte: '0xFF_TAMPERED', tampered: true };
      }
    }

    tamperBlock(selectedSeq, mutatedPayload);
    // Immediately re-run verify to illustrate real-time red alert
    await verifyChain();
  };

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      <div className="flex items-center justify-between pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400">
            <Bug className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                Tamper Mutation Injector (Adversarial Simulation)
              </h3>
              <Badge variant="rose" size="sm">
                Live Attack Sandbox
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Select any past block, flip 1 byte in canonical JSON, and witness instant fail-closed chain detection
            </p>
          </div>
        </div>
      </div>

      {auditBackendError && (
        <div className="p-3 rounded-xl border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs font-mono flex items-center gap-2">
          <WifiOff className="w-4 h-4 text-rose-400 shrink-0" />
          <span>
            {auditBackendError} The mutation below is applied to the local sandbox only — re-verification against
            the real chain requires the live backend.
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs font-mono">
        {/* Step 1: Select Block */}
        <div className="p-4 rounded-xl bg-[#0e131d] border border-slate-800 space-y-2">
          <label className="text-slate-300 font-sans font-semibold flex items-center gap-1.5">
            <span className="w-5 h-5 rounded-full bg-indigo-600/40 border border-indigo-500/50 flex items-center justify-center text-[10px]">
              1
            </span>
            Target Block Sequence:
          </label>
          <select
            value={selectedSeq}
            onChange={(e) => setSelectedSeq(Number(e.target.value))}
            className="w-full px-3 py-2 rounded-xl bg-[#141a27] border border-slate-700 text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
          >
            {auditEvents.map((e) => (
              <option key={e.seq} value={e.seq}>
                Seq #{e.seq} — {e.type} {e.tampered ? '(CORRUPTED)' : ''}
              </option>
            ))}
          </select>
          {targetEvent && (
            <p className="text-[11px] text-slate-400">
              Mandate: <span className="text-slate-200">{targetEvent.mandate_id}</span>
            </p>
          )}
        </div>

        {/* Step 2: Mutation Type */}
        <div className="p-4 rounded-xl bg-[#0e131d] border border-slate-800 space-y-2">
          <label className="text-slate-300 font-sans font-semibold flex items-center gap-1.5">
            <span className="w-5 h-5 rounded-full bg-indigo-600/40 border border-indigo-500/50 flex items-center justify-center text-[10px]">
              2
            </span>
            Payload Mutation Vector:
          </label>
          <select
            value={tamperField}
            onChange={(e) => setTamperField(e.target.value as any)}
            className="w-full px-3 py-2 rounded-xl bg-[#141a27] border border-slate-700 text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
          >
            <option value="price">Tamper Price to $999.00</option>
            <option value="amount">Tamper Amount String</option>
            <option value="limits">Inflate Mandate Limits to $9,999</option>
            <option value="custom">Inject Raw Corrupted Byte</option>
          </select>
          <input
            type="text"
            value={customValue}
            onChange={(e) => setCustomValue(e.target.value)}
            placeholder="Custom mutation value..."
            className="w-full px-3 py-1.5 rounded-lg bg-[#141a27] border border-slate-800 text-slate-200 font-mono text-[11px]"
          />
        </div>

        {/* Step 3: Trigger Attack */}
        <div className="p-4 rounded-xl bg-[#0e131d] border border-slate-800 flex flex-col justify-between gap-3">
          <label className="text-slate-300 font-sans font-semibold flex items-center gap-1.5">
            <span className="w-5 h-5 rounded-full bg-rose-600/40 border border-rose-500/50 flex items-center justify-center text-[10px] text-rose-300">
              3
            </span>
            Execute Mutation:
          </label>
          <div className="flex flex-col gap-2">
            <button
              onClick={handleInject}
              className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-500 hover:to-red-500 text-white font-bold text-xs shadow-lg shadow-rose-950/50 transition-all cursor-pointer"
            >
              <Zap className="w-4 h-4" />
              Inject Mutation & Re-verify
            </button>
            <button
              onClick={() => restoreLedger()}
              className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg border border-slate-700 bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Restore Genesis Clean State
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
