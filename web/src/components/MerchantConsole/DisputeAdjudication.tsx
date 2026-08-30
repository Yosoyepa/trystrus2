import React from 'react';
import { useApp } from '../../context/AppContext';
import { Scale, ShieldCheck, FileCheck } from 'lucide-react';
import { Badge } from '../common/Badge';

export const DisputeAdjudication: React.FC = () => {
  const { disputes, submitDisputeEvidence, adjudicateDispute } = useApp();

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      <div className="flex items-center justify-between pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-400">
            <Scale className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                Dispute Simulation & Cryptographic Adjudication Engine
              </h3>
              <Badge variant="amber" size="sm">
                Non-Repudiation Defense
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Submit proof packs (SD-JWT + JWS) to defeat false "Unauthorized Transaction" chargebacks
            </p>
          </div>
        </div>
      </div>

      {disputes.length === 0 ? (
        <div className="p-8 rounded-xl bg-[#0c1018] border border-dashed border-slate-800 text-center text-xs text-slate-500 font-mono">
          No active disputes. Open a dispute on any captured transaction in the Yuno Rail Simulator tab above.
        </div>
      ) : (
        <div className="space-y-4">
          {disputes.map((dsp) => (
            <div
              key={dsp.dispute_id}
              className="p-4 sm:p-5 rounded-xl bg-[#0e131d] border border-slate-800 space-y-4 font-mono text-xs"
            >
              <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-800">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-slate-200">Dispute #{dsp.dispute_id}</span>
                  <Badge variant={dsp.status === 'MERCHANT_WON' ? 'emerald' : dsp.status === 'OPEN' ? 'amber' : 'indigo'} size="sm">
                    {dsp.status}
                  </Badge>
                </div>
                <span className="text-slate-400">
                  Claim Reason: <strong className="text-rose-400 font-sans">{dsp.reason}</strong>
                </span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[11px] text-slate-300">
                <div className="p-2.5 rounded bg-[#141a27] border border-slate-800">
                  <span className="text-slate-500 block text-[9px]">DISPUTED AMOUNT</span>
                  <span className="text-slate-200 font-bold">${dsp.amount} {dsp.currency}</span>
                </div>
                <div className="p-2.5 rounded bg-[#141a27] border border-slate-800">
                  <span className="text-slate-500 block text-[9px]">ORIGINAL CAPTURE</span>
                  <span className="text-slate-200 truncate block">{dsp.capture_id}</span>
                </div>
                <div className="p-2.5 rounded bg-[#141a27] border border-slate-800">
                  <span className="text-slate-500 block text-[9px]">PROOF ATTESTATION</span>
                  <span className={dsp.evidence_submitted ? 'text-emerald-400 font-bold' : 'text-amber-400 font-bold'}>
                    {dsp.evidence_submitted ? 'Evidence Submitted' : 'Awaiting Proof Pack'}
                  </span>
                </div>
              </div>

              {dsp.evidence_bundle && (
                <div className="p-3 rounded-lg bg-indigo-950/20 border border-indigo-500/30 space-y-1.5 text-[11px]">
                  <div className="flex items-center gap-1.5 text-indigo-300 font-semibold">
                    <FileCheck className="w-3.5 h-3.5" /> Assembled Evidence Bundle:
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-slate-300">
                    <div>Mandate JTI: <span className="text-white">{dsp.evidence_bundle.mandate_jti}</span></div>
                    <div>Audit Chain Seq: <span className="text-white">#{dsp.evidence_bundle.audit_chain_seq}</span></div>
                  </div>
                </div>
              )}

              {/* Actions */}
              <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
                {!dsp.evidence_submitted ? (
                  <button
                    onClick={() => submitDisputeEvidence(dsp.dispute_id)}
                    className="flex items-center gap-2 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs transition-colors cursor-pointer"
                  >
                    <FileCheck className="w-4 h-4" /> Submit Cryptographic Evidence Pack
                  </button>
                ) : (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => adjudicateDispute(dsp.dispute_id, 'MERCHANT_WON')}
                      className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs transition-colors cursor-pointer"
                    >
                      <ShieldCheck className="w-4 h-4" /> Adjudicate: Merchant Won (100% Non-Repudiation)
                    </button>
                    <button
                      onClick={() => adjudicateDispute(dsp.dispute_id, 'BUYER_WON')}
                      className="px-3 py-2 rounded-xl border border-slate-700 hover:bg-slate-800 text-slate-400 text-xs transition-colors cursor-pointer"
                    >
                      Refund Buyer
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
