import React, { useState } from 'react';
import { useApp } from '../../context/AppContext';
import { Fingerprint, Shield } from 'lucide-react';

interface MandateCreatorProps {
  onCreated?: () => void;
}

export const MandateCreator: React.FC<MandateCreatorProps> = ({ onCreated }) => {
  const { createMandate } = useApp();
  const [maxPerTxn, setMaxPerTxn] = useState(150);
  const [totalBudget, setTotalBudget] = useState(400);
  const [maxTxnCount, setMaxTxnCount] = useState(3);
  const [category, setCategory] = useState('flights');
  const [merchant, setMerchant] = useState('vuelaya');
  const [jsonLogicRule, setJsonLogicRule] = useState('{"<": [{"var": "offer.price"}, 150]}');
  const [isSigning, setIsSigning] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSigning(true);

    let parsedConditions = { '<': [{ var: 'offer.price' }, maxPerTxn] };
    try {
      parsedConditions = JSON.parse(jsonLogicRule);
    } catch {
      // fallback
    }

    setTimeout(async () => {
      await createMandate({
        sub: 'usr_marta',
        agent: 'agt_flights',
        currency: 'USD',
        scope: {
          categories: [category],
          merchants: [merchant],
        },
        conditions: parsedConditions,
        limits: {
          max_per_txn: Number(maxPerTxn),
          total_budget: Number(totalBudget),
          max_txn: { count: Number(maxTxnCount), period: 'month' },
        },
      });

      setIsSigning(false);
      if (onCreated) onCreated();
    }, 500);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4 text-xs font-mono">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="text-slate-400 block mb-1 text-[11px]">Max Per Transaction ($)</label>
          <input
            type="number"
            value={maxPerTxn}
            onChange={(e) => setMaxPerTxn(Number(e.target.value))}
            min={1}
            className="w-full px-3 py-2 rounded-xl bg-[#0e131d] border border-slate-800 text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
            required
          />
        </div>
        <div>
          <label className="text-slate-400 block mb-1 text-[11px]">Total Budget ($)</label>
          <input
            type="number"
            value={totalBudget}
            onChange={(e) => setTotalBudget(Number(e.target.value))}
            min={1}
            className="w-full px-3 py-2 rounded-xl bg-[#0e131d] border border-slate-800 text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
            required
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div>
          <label className="text-slate-400 block mb-1 text-[11px]">Max Txn Count</label>
          <input
            type="number"
            value={maxTxnCount}
            onChange={(e) => setMaxTxnCount(Number(e.target.value))}
            min={1}
            className="w-full px-3 py-2 rounded-xl bg-[#0e131d] border border-slate-800 text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
            required
          />
        </div>
        <div>
          <label className="text-slate-400 block mb-1 text-[11px]">Allowed Category</label>
          <input
            type="text"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="w-full px-3 py-2 rounded-xl bg-[#0e131d] border border-slate-800 text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
            required
          />
        </div>
        <div>
          <label className="text-slate-400 block mb-1 text-[11px]">Allowed Merchant</label>
          <input
            type="text"
            value={merchant}
            onChange={(e) => setMerchant(e.target.value)}
            className="w-full px-3 py-2 rounded-xl bg-[#0e131d] border border-slate-800 text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
            required
          />
        </div>
      </div>

      <div>
        <label className="text-slate-400 block mb-1 text-[11px]">JsonLogic Deterministic Rule</label>
        <input
          type="text"
          value={jsonLogicRule}
          onChange={(e) => setJsonLogicRule(e.target.value)}
          className="w-full px-3 py-2 rounded-xl bg-[#0e131d] border border-slate-800 text-slate-200 focus:outline-none focus:border-indigo-500 font-mono text-[11px]"
          required
        />
      </div>

      <div className="p-3 rounded-xl bg-indigo-950/20 border border-indigo-500/30 text-[11px] text-slate-300 font-sans space-y-1">
        <div className="flex items-center gap-1.5 font-semibold text-indigo-400">
          <Shield className="w-3.5 h-3.5" /> Selective Disclosures (RFC 9901)
        </div>
        <p className="text-slate-400">
          Includes masked salt digests for <span className="font-mono text-slate-300">email</span> and{' '}
          <span className="font-mono text-slate-300">shipping_address</span>. Agent discloses them only at checkout if demanded.
        </p>
      </div>

      <button
        type="submit"
        disabled={isSigning}
        className="w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-bold text-xs shadow-lg shadow-indigo-950/50 transition-all cursor-pointer disabled:opacity-50"
      >
        {isSigning ? (
          <>
            <Fingerprint className="w-4 h-4 animate-spin" /> Signing SD-JWT with Passkey...
          </>
        ) : (
          <>
            <Fingerprint className="w-4 h-4" /> Issue & Sign Mandate SD-JWT
          </>
        )}
      </button>
    </form>
  );
};
