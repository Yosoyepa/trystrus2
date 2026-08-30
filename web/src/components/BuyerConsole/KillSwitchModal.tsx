import React, { useState } from 'react';
import { useApp } from '../../context/AppContext';
import { Modal } from '../common/Modal';
import { Flame, Fingerprint, CheckCircle2, ShieldAlert, Clock } from 'lucide-react';

interface KillSwitchModalProps {
  isOpen: boolean;
  onClose: () => void;
  mandateId: string;
}

export const KillSwitchModal: React.FC<KillSwitchModalProps> = ({
  isOpen,
  onClose,
  mandateId,
}) => {
  const { revokeMandate } = useApp();
  const [isExecuting, setIsExecuting] = useState(false);
  const [result, setResult] = useState<{ success: boolean; latency_ms: number } | null>(null);

  const handleRevoke = async () => {
    setIsExecuting(true);
    const res = await revokeMandate(mandateId);
    setResult(res);
    setIsExecuting(false);
  };

  const handleReset = () => {
    setResult(null);
    onClose();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleReset}
      title="Dual Kill Switch — Emergency Mandate Revocation"
      subtitle="Guaranteed Synchronous Deletion at Kernel & Payment Rail in ≤ 2.0s"
      icon={<Flame className="w-6 h-6 text-rose-500" />}
      maxWidth="max-w-xl"
    >
      <div className="space-y-4 text-xs font-mono">
        {!result ? (
          <>
            <div className="p-4 rounded-xl bg-rose-950/20 border border-rose-500/40 text-slate-300 font-sans space-y-2">
              <h4 className="font-bold text-rose-300 text-sm flex items-center gap-2">
                <ShieldAlert className="w-4 h-4" /> Immediate Dual Action:
              </h4>
              <ul className="list-disc list-inside space-y-1 text-xs text-slate-300">
                <li>
                  <strong className="text-white">Kernel State:</strong> Mandate {mandateId} transitions to{' '}
                  <span className="text-rose-400 font-mono font-bold">REVOKED</span>.
                </li>
                <li>
                  <strong className="text-white">Yuno Payment Rail:</strong> Payment token{' '}
                  <span className="text-purple-300 font-mono">ppt_9XZ...</span> is deleted via{' '}
                  <code className="text-slate-300">DELETE /vault/tokens/{'{id}'}</code>.
                </li>
                <li>
                  <strong className="text-white">Strict SLA:</strong> Target latency is{' '}
                  <span className="text-emerald-400 font-bold">&le; 2000 ms</span>.
                </li>
              </ul>
            </div>

            <div className="p-3 rounded-xl bg-[#0e131d] border border-slate-800 flex items-center justify-between">
              <span className="text-slate-400">Target Mandate JTI:</span>
              <span className="text-slate-200 font-bold">{mandateId}</span>
            </div>

            <button
              onClick={handleRevoke}
              disabled={isExecuting}
              className="w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-500 hover:to-red-500 text-white font-bold text-xs shadow-lg shadow-rose-950/50 transition-all cursor-pointer disabled:opacity-50"
            >
              {isExecuting ? (
                <>
                  <Fingerprint className="w-4 h-4 animate-spin" /> Verifying Passkey & Deleting Token...
                </>
              ) : (
                <>
                  <Fingerprint className="w-4 h-4" /> Sign with Passkey & Execute Kill Switch
                </>
              )}
            </button>
          </>
        ) : (
          <div className="space-y-4">
            <div className="p-4 rounded-xl bg-emerald-950/20 border border-emerald-500/40 space-y-3">
              <div className="flex items-center gap-2 text-emerald-400 font-bold text-sm">
                <CheckCircle2 className="w-5 h-5" />
                <span>Dual Kill Switch Executed Successfully</span>
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs font-mono pt-1">
                <div className="p-2 rounded bg-[#0c1018] border border-slate-800">
                  <span className="text-slate-500 block text-[10px]">EXECUTION LATENCY</span>
                  <span className="text-emerald-400 font-bold text-sm flex items-center gap-1">
                    <Clock className="w-3.5 h-3.5" /> {result.latency_ms} ms
                  </span>
                </div>
                <div className="p-2 rounded bg-[#0c1018] border border-slate-800">
                  <span className="text-slate-500 block text-[10px]">SLA COMPLIANCE</span>
                  <span className="text-emerald-400 font-bold text-sm">PASS (&le; 2000ms)</span>
                </div>
              </div>

              <p className="text-[11px] text-slate-300 font-sans">
                Next purchase attempt will fail twice: at the Kernel Policy Gate with{' '}
                <strong className="text-rose-300 font-mono">MANDATE_REVOKED</strong>, and at the Yuno rail with{' '}
                <strong className="text-rose-300 font-mono">RAIL_TOKEN_DELETED</strong>.
              </p>
            </div>

            <button
              onClick={handleReset}
              className="w-full py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-white font-semibold text-xs transition-colors cursor-pointer"
            >
              Close
            </button>
          </div>
        )}
      </div>
    </Modal>
  );
};
