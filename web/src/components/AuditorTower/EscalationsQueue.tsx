import React, { useState, useEffect } from 'react';
import { useApp } from '../../context/AppContext';
import {
  Clock,
  CheckCircle2,
  XCircle,
  Fingerprint,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import { Escalation } from '../../types';

export const EscalationsQueue: React.FC = () => {
  const { escalations, resolveEscalation } = useApp();
  const [stickyOption, setStickyOption] = useState<{ [id: string]: boolean }>({});
  const [isSigning, setIsSigning] = useState<string | null>(null);

  const pendingEscalations = escalations.filter((e) => e.status === 'pending');
  const resolvedEscalations = escalations.filter((e) => e.status !== 'pending');

  const handleApprove = async (id: string) => {
    setIsSigning(id);
    // Simulate WebAuthn biometric assertion delay
    setTimeout(async () => {
      await resolveEscalation(id, 'APPROVE', stickyOption[id] || false);
      setIsSigning(null);
    }, 450);
  };

  const handleReject = async (id: string) => {
    await resolveEscalation(id, 'REJECT');
  };

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      <div className="flex items-center justify-between pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-400">
            <Clock className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                Human-in-the-Loop Escalations Queue
              </h3>
              <Badge variant={pendingEscalations.length > 0 ? 'amber' : 'slate'} size="sm" pulse={pendingEscalations.length > 0}>
                {pendingEscalations.length} Pending
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              120s Fail-Closed Timeout · WebAuthn Biometric Assertion · Telegram / Console Webhooks
            </p>
          </div>
        </div>
      </div>

      {/* Pending List */}
      {pendingEscalations.length === 0 ? (
        <div className="p-8 rounded-xl bg-[#0c1018] border border-dashed border-slate-800 text-center space-y-2">
          <CheckCircle2 className="w-8 h-8 text-emerald-400 mx-auto opacity-75" />
          <h4 className="text-sm font-semibold text-slate-200">No Pending Escalations</h4>
          <p className="text-xs text-slate-500 max-w-sm mx-auto">
            All agent proposals have conformed directly to mandate limits, or have already been resolved.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {pendingEscalations.map((esc) => (
            <EscalationCard
              key={esc.escalation_id}
              esc={esc}
              isSigning={isSigning === esc.escalation_id}
              sticky={stickyOption[esc.escalation_id] || false}
              setSticky={(val) => setStickyOption((prev) => ({ ...prev, [esc.escalation_id]: val }))}
              onApprove={() => handleApprove(esc.escalation_id)}
              onReject={() => handleReject(esc.escalation_id)}
            />
          ))}
        </div>
      )}

      {/* History */}
      {resolvedEscalations.length > 0 && (
        <div className="pt-4 border-t border-slate-800/80 space-y-3">
          <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            Recently Resolved ({resolvedEscalations.length})
          </h4>
          <div className="space-y-2">
            {resolvedEscalations.slice(0, 3).map((esc) => (
              <div
                key={esc.escalation_id}
                className="p-3 rounded-xl bg-[#0c1018] border border-slate-800/80 flex items-center justify-between text-xs font-mono"
              >
                <div className="flex items-center gap-2.5">
                  <Badge
                    variant={esc.resolution?.decision === 'APPROVE' ? 'emerald' : 'rose'}
                    size="sm"
                  >
                    {esc.resolution?.decision || esc.status}
                  </Badge>
                  <span className="text-slate-300 font-sans">{esc.offer_title || esc.escalation_id}</span>
                </div>
                <span className="text-slate-500 text-[11px]">
                  by {esc.resolution?.approver || 'system'} ({esc.resolution?.channel || 'timeout'})
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

interface EscalationCardProps {
  esc: Escalation;
  isSigning: boolean;
  sticky: boolean;
  setSticky: (val: boolean) => void;
  onApprove: () => void;
  onReject: () => void;
}

const EscalationCard: React.FC<EscalationCardProps> = ({
  esc,
  isSigning,
  sticky,
  setSticky,
  onApprove,
  onReject,
}) => {
  const [secondsRemaining, setSecondsRemaining] = useState<number>(120);

  useEffect(() => {
    const target = new Date(esc.timeout_at).getTime();
    const update = () => {
      const remaining = Math.max(0, Math.round((target - Date.now()) / 1000));
      setSecondsRemaining(remaining);
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [esc.timeout_at]);

  const getTimerBadgeVariant = () => {
    if (secondsRemaining > 60) return 'emerald' as const;
    if (secondsRemaining > 30) return 'amber' as const;
    return 'rose' as const;
  };

  return (
    <div className="p-4 sm:p-5 rounded-xl bg-[#0e131d] border border-amber-500/40 shadow-lg shadow-amber-950/20 space-y-4">
      {/* Top row with Timer */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <Badge variant="amber" size="sm">
            {esc.diff.reason || 'AMOUNT_EXCEEDS_PER_TXN'}
          </Badge>
          <span className="text-xs font-mono text-slate-400">ID: {esc.escalation_id}</span>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400 font-mono">Timeout Deadline:</span>
          <Badge variant={getTimerBadgeVariant()} size="sm" pulse={secondsRemaining <= 30}>
            <Clock className="w-3 h-3" />
            {secondsRemaining}s left (Fail-Closed)
          </Badge>
        </div>
      </div>

      {/* Side by side proposal diff */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono">
        <div className="p-3 rounded-lg bg-[#141a27] border border-slate-800">
          <span className="text-[10px] uppercase text-slate-400 block mb-1">Mandate Policy Limit</span>
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-bold text-slate-200">
              ${(esc.diff.value ?? 0).toFixed(2)} {esc.diff.currency || 'USD'}
            </span>
            <span className="text-slate-500 font-sans text-[11px]">(max_per_txn)</span>
          </div>
        </div>

        <div className="p-3 rounded-lg bg-amber-950/20 border border-amber-500/40">
          <span className="text-[10px] uppercase text-amber-400 block mb-1">Agent Proposed Purchase</span>
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-bold text-amber-300">
              ${(esc.diff.attempted ?? 0).toFixed(2)} {esc.diff.currency || 'USD'}
            </span>
            <span className="text-rose-400 font-sans text-[11px] font-semibold">
              (+${((esc.diff.attempted ?? 0) - (esc.diff.value ?? 0)).toFixed(2)} delta)
            </span>
          </div>
        </div>
      </div>

      {esc.offer_title && (
        <div className="text-xs text-slate-300 font-sans flex items-center gap-2">
          <span className="text-slate-500 font-mono">Flight:</span>
          <strong className="text-slate-200">{esc.offer_title}</strong>
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
        <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={sticky}
            onChange={(e) => setSticky(e.target.checked)}
            className="rounded border-slate-700 bg-slate-800 text-indigo-600 focus:ring-0 w-4 h-4 cursor-pointer"
          />
          <span>Issue sticky derived mini-mandate for this threshold</span>
        </label>

        <div className="flex items-center gap-2.5 w-full sm:w-auto">
          <button
            onClick={onReject}
            className="flex-1 sm:flex-none flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl border border-rose-500/40 bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 text-xs font-semibold transition-colors cursor-pointer"
          >
            <XCircle className="w-4 h-4" /> Reject
          </button>

          <button
            onClick={onApprove}
            disabled={isSigning}
            className="flex-1 sm:flex-none flex items-center justify-center gap-2 px-5 py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white text-xs font-semibold shadow-lg shadow-emerald-950/40 transition-all cursor-pointer disabled:opacity-50"
          >
            {isSigning ? (
              <>
                <Fingerprint className="w-4 h-4 animate-spin text-emerald-200" /> Signing Passkey...
              </>
            ) : (
              <>
                <Fingerprint className="w-4 h-4 text-emerald-200" /> WebAuthn Approve
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
