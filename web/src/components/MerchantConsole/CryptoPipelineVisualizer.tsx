import React, { useState } from 'react';
import { useApp } from '../../context/AppContext';
import {
  ShieldCheck,
  Play,
  CheckCircle2,
  XCircle,
  Clock,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import { api } from '../../services/api';

export const CryptoPipelineVisualizer: React.FC = () => {
  const { activeMandate, offers } = useApp();
  const [selectedOfferId, setSelectedOfferId] = useState('ofr_cor_130');
  const [testMode, setTestMode] = useState<'normal' | 'corrupt_sig' | 'corrupt_cart' | 'price_hike'>('normal');
  const [isRunning, setIsRunning] = useState(false);
  const [steps, setSteps] = useState<Array<{ step: number; name: string; status: 'passed' | 'failed' | 'running' | 'pending'; details: string }>>([
    { step: 1, name: '1. Issuer SD-JWT check', status: 'pending', details: 'Verify SD-JWT signature against published JWKS Ed25519 issuer key.' },
    { step: 2, name: '2. Agent Detached JWS check', status: 'pending', details: 'Verify proof-of-possession EdDSA detached signature on JCS canonical intent.' },
    { step: 3, name: '3. Price exact cent match', status: 'pending', details: 'Validate intent amount matches merchant catalog offer price cent-by-cent.' },
    { step: 4, name: '4. AP2 Cart hash binding', status: 'pending', details: 'Check checkout_hash matches SHA-256(checkout_jwt).' },
    { step: 5, name: '5. Kernel Policy Gate CAS reservation', status: 'pending', details: 'Deterministic policy evaluate & atomic CAS budget locking (/mandates/{id}/verify).' },
    { step: 6, name: '6. Yuno AP2 Rail settlement', status: 'pending', details: 'Execute payment capture on vaulted payment token ppt_9XZ...' },
    { step: 7, name: '7. Order receipt & signed webhook emission', status: 'pending', details: 'Issue signed merchant receipt and emit outbox event to ledger.' },
  ]);

  const handleRunPipeline = async () => {
    setIsRunning(true);
    const mandateJti = activeMandate?.jti || 'mdt_01J8Z9X2K3';

    // Reset steps to running
    setSteps((prev) => prev.map((s) => ({ ...s, status: 'pending' })));

    const opts = {
      corruptedSignature: testMode === 'corrupt_sig',
      corruptedCartHash: testMode === 'corrupt_cart',
      overridePrice: testMode === 'price_hike' ? '180.00' : undefined,
    };

    const res = await api.executePurchase(mandateJti, selectedOfferId, opts);
    setSteps(res.pipelineSteps);
    setIsRunning(false);
  };

  const getStepIcon = (status: string) => {
    switch (status) {
      case 'passed':
        return <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />;
      case 'failed':
        return <XCircle className="w-5 h-5 text-rose-400 shrink-0" />;
      case 'running':
        return <Clock className="w-5 h-5 text-indigo-400 animate-spin shrink-0" />;
      default:
        return <div className="w-5 h-5 rounded-full border-2 border-slate-700 shrink-0" />;
    }
  };

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
            <ShieldCheck className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                7-Step Merchant Cryptographic Verification Pipeline
              </h3>
              <Badge variant="indigo" size="sm">
                Pay-Time Enforcement
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              The merchant verifies the mandate itself offline before charging any rail instrument
            </p>
          </div>
        </div>

        <button
          onClick={handleRunPipeline}
          disabled={isRunning}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-bold text-xs shadow-lg shadow-indigo-950/50 transition-all cursor-pointer disabled:opacity-50"
        >
          {isRunning ? <Clock className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-white" />}
          Execute 7-Step Pipeline
        </button>
      </div>

      {/* Pipeline Controls */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs font-mono">
        <div className="p-3 rounded-xl bg-[#0e131d] border border-slate-800 space-y-1.5">
          <label className="text-slate-400 block text-[11px]">Select Flight Offer to Test:</label>
          <select
            value={selectedOfferId}
            onChange={(e) => setSelectedOfferId(e.target.value)}
            className="w-full px-3 py-1.5 rounded-lg bg-[#141a27] border border-slate-700 text-slate-200"
          >
            {offers.map((o) => (
              <option key={o.offer_id} value={o.offer_id}>
                {o.flight_num || o.offer_id}: {o.title} (${o.amount})
              </option>
            ))}
          </select>
        </div>

        <div className="p-3 rounded-xl bg-[#0e131d] border border-slate-800 space-y-1.5">
          <label className="text-slate-400 block text-[11px]">Pipeline Test Condition:</label>
          <select
            value={testMode}
            onChange={(e) => setTestMode(e.target.value as any)}
            className="w-full px-3 py-1.5 rounded-lg bg-[#141a27] border border-slate-700 text-slate-200"
          >
            <option value="normal">Standard Compliant Purchase (Pass All 7)</option>
            <option value="corrupt_sig">Simulate Forged Agent Key (Fail Step 2)</option>
            <option value="price_hike">Simulate Price Mismatch Drift (Fail Step 3)</option>
            <option value="corrupt_cart">Simulate AP2 Cart Tampering (Fail Step 4)</option>
          </select>
        </div>
      </div>

      {/* 7-Step Pipeline Steps List */}
      <div className="space-y-2.5">
        {steps.map((step, idx) => {
          return (
            <div
              key={step.step}
              className={`p-3.5 rounded-xl border transition-all flex items-start gap-3.5 ${
                step.status === 'passed'
                  ? 'bg-emerald-950/20 border-emerald-500/40 text-emerald-200'
                  : step.status === 'failed'
                  ? 'bg-rose-950/20 border-rose-500/40 text-rose-200'
                  : step.status === 'running'
                  ? 'bg-indigo-950/30 border-indigo-500/50 text-indigo-200 shadow-md shadow-indigo-950'
                  : 'bg-[#0c1018] border-slate-800/80 text-slate-400'
              }`}
            >
              {getStepIcon(step.status)}
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-bold font-mono text-slate-100">{step.name}</h4>
                  <Badge
                    variant={
                      step.status === 'passed'
                        ? 'emerald'
                        : step.status === 'failed'
                        ? 'rose'
                        : step.status === 'running'
                        ? 'indigo'
                        : 'slate'
                    }
                    size="sm"
                  >
                    {step.status.toUpperCase()}
                  </Badge>
                </div>
                <p className="text-[11px] mt-1 font-sans text-slate-300 leading-relaxed">{step.details}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
