import React from 'react';
import { useApp } from '../../context/AppContext';
import { CreditCard } from 'lucide-react';
import { Badge } from '../common/Badge';
import { HashBadge } from '../common/HashBadge';

export const YunoRailSimulator: React.FC = () => {
  const { vaultTokens, receipts, openDispute } = useApp();

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-400">
            <CreditCard className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                Yuno AP2 Simulated Payment Rail
              </h3>
              <Badge variant="cyan" size="sm">
                Decision #24 (AP2 Simulation)
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Vaulted Tokens · Real-Time Token Purge on Revocation · Settlement Captures
            </p>
          </div>
        </div>
      </div>

      {/* Section 1: Vaulted Tokens */}
      <div className="space-y-3">
        <h4 className="text-xs font-bold uppercase tracking-wider text-slate-300 font-mono flex items-center gap-2">
          <span>Vaulted Payment Instruments (Opaque Payment Tokens)</span>
          <Badge variant="indigo" size="sm">{vaultTokens.length}</Badge>
        </h4>

        <div className="overflow-x-auto rounded-xl border border-slate-800 bg-[#0c1018]">
          <table className="w-full text-left text-xs border-collapse font-mono">
            <thead>
              <tr className="bg-[#111724] border-b border-slate-800 text-slate-400 uppercase text-[10px]">
                <th className="py-2.5 px-3">Token Ref ID</th>
                <th className="py-2.5 px-3">Card / Instrument</th>
                <th className="py-2.5 px-3">Bound Mandate</th>
                <th className="py-2.5 px-3">Status</th>
                <th className="py-2.5 px-3 text-right">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {vaultTokens.map((token) => (
                <tr key={token.token_id} className="hover:bg-slate-800/40">
                  <td className="py-2.5 px-3">
                    <HashBadge hash={token.token_id} variant="purple" lead={6} trail={4} />
                  </td>
                  <td className="py-2.5 px-3 text-slate-200">
                    {token.card_brand} (•••• {token.last4})
                  </td>
                  <td className="py-2.5 px-3 text-indigo-300">
                    {token.mandate_jti}
                  </td>
                  <td className="py-2.5 px-3">
                    <Badge variant={token.status === 'ACTIVE' ? 'emerald' : 'rose'} size="sm">
                      {token.status}
                    </Badge>
                  </td>
                  <td className="py-2.5 px-3 text-right text-slate-400 text-[11px]">
                    {new Date(token.created_at).toLocaleTimeString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Section 2: Captured Payments & Receipts */}
      <div className="space-y-3 pt-2">
        <h4 className="text-xs font-bold uppercase tracking-wider text-slate-300 font-mono flex items-center gap-2">
          <span>Settled Captures & Merchant Receipts</span>
          <Badge variant="emerald" size="sm">{receipts.length}</Badge>
        </h4>

        {receipts.length === 0 ? (
          <div className="p-6 rounded-xl bg-[#0c1018] border border-dashed border-slate-800 text-center text-xs text-slate-500 font-mono">
            No captures recorded yet in this session. Book a flight in the Buyer Console or Demo Runner to view captures.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {receipts.map((rcpt) => (
              <div
                key={rcpt.capture_id}
                className="p-4 rounded-xl bg-[#0e131d] border border-slate-800 space-y-3 font-mono text-xs"
              >
                <div className="flex items-center justify-between">
                  <span className="font-bold text-slate-200">{rcpt.offer_title || 'Flight Purchase'}</span>
                  <Badge variant="emerald" size="sm">SETTLED</Badge>
                </div>

                <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-300">
                  <div className="p-2 rounded bg-[#141a27] border border-slate-800">
                    <span className="text-slate-500 block text-[9px]">AMOUNT</span>
                    <span className="text-emerald-400 font-bold">${rcpt.amount} {rcpt.currency}</span>
                  </div>
                  <div className="p-2 rounded bg-[#141a27] border border-slate-800">
                    <span className="text-slate-500 block text-[9px]">CAPTURE ID</span>
                    <span className="text-slate-300 truncate block">{rcpt.capture_id}</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[10px] text-slate-500">
                    {new Date(rcpt.captured_at).toLocaleTimeString()}
                  </span>
                  <button
                    onClick={() => openDispute(rcpt.capture_id)}
                    className="text-[11px] text-amber-400 hover:text-amber-300 underline cursor-pointer"
                  >
                    Simulate Dispute
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
