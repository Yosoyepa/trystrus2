import React, { useState } from 'react';
import { MandateView } from '../../types';
import {
  Key,
  Eye,
  EyeOff,
  Flame,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import { HashBadge } from '../common/HashBadge';
import { ProgressBar } from '../common/ProgressBar';
import { CodeBlock } from '../common/CodeBlock';

interface MandateCardProps {
  mandate: MandateView;
  onRevokeClick: (mandateId: string) => void;
}

export const MandateCard: React.FC<MandateCardProps> = ({ mandate, onRevokeClick }) => {
  const [showSDJWT, setShowSDJWT] = useState(false);

  const getStatusBadge = (status: MandateView['status']) => {
    switch (status) {
      case 'active':
        return <Badge variant="emerald" pulse>ACTIVE</Badge>;
      case 'revoked':
        return <Badge variant="rose">REVOKED</Badge>;
      case 'suspended':
        return <Badge variant="amber">SUSPENDED</Badge>;
      case 'exhausted':
        return <Badge variant="purple">EXHAUSTED</Badge>;
      default:
        return <Badge variant="slate">DRAFT</Badge>;
    }
  };

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
            <Key className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                Purchase Mandate SD-JWT
              </h3>
              {getStatusBadge(mandate.status)}
            </div>
            <p className="text-xs text-slate-400 mt-0.5 font-mono">
              JTI: <span className="text-slate-200">{mandate.jti}</span>
            </p>
          </div>
        </div>

        {mandate.status === 'active' && (
          <button
            onClick={() => onRevokeClick(mandate.jti)}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl border border-rose-500/40 bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 text-xs font-semibold transition-colors cursor-pointer"
          >
            <Flame className="w-4 h-4 text-rose-400" />
            Dual Kill Switch (Revoke &lt;2s)
          </button>
        )}
      </div>

      {/* Budget Progress Bar */}
      <div className="p-4 rounded-xl bg-[#0e131d] border border-slate-800 space-y-2">
        <div className="flex justify-between items-center text-xs font-mono text-slate-400">
          <span>Active Budget Allocation</span>
          <span>
            Txn Count: <strong className="text-white">{mandate.txn_count_period}</strong> /{' '}
            {mandate.limits.max_txn.count} per {mandate.limits.max_txn.period}
          </span>
        </div>
        <ProgressBar
          spent={mandate.spent}
          reserved={mandate.reserved}
          total={mandate.limits.total_budget}
        />
      </div>

      {/* Scope and Constraints Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs font-mono">
        <div className="p-3 rounded-xl bg-[#0e131d] border border-slate-800">
          <span className="text-[10px] uppercase text-slate-500 block">Max Per Transaction</span>
          <span className="text-base font-bold text-slate-200">
            ${mandate.limits.max_per_txn.toFixed(2)} USD
          </span>
        </div>
        <div className="p-3 rounded-xl bg-[#0e131d] border border-slate-800">
          <span className="text-[10px] uppercase text-slate-500 block">Scope Categories</span>
          <span className="text-sm font-semibold text-indigo-300">
            {mandate.claims?.scope.categories.join(', ') || 'flights'}
          </span>
        </div>
        <div className="p-3 rounded-xl bg-[#0e131d] border border-slate-800">
          <span className="text-[10px] uppercase text-slate-500 block">Vault Token (AP2 Rail)</span>
          <HashBadge hash={mandate.payment_method_ref} variant="purple" lead={6} trail={4} />
        </div>
      </div>

      {/* SD-JWT Preview Toggle */}
      <div className="pt-2">
        <button
          onClick={() => setShowSDJWT(!showSDJWT)}
          className="flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors font-mono"
        >
          {showSDJWT ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          <span>{showSDJWT ? 'Hide Raw SD-JWT Token Structure' : 'Inspect Raw SD-JWT Token Structure'}</span>
        </button>

        {showSDJWT && (
          <div className="mt-3 space-y-3">
            {mandate.sd_jwt && (
              <div className="p-3 rounded-xl bg-[#0c1018] border border-slate-800 font-mono text-[11px] text-slate-300 break-all leading-relaxed">
                <span className="text-slate-500 block text-[10px] uppercase mb-1">SD-JWT Wire Token:</span>
                {mandate.sd_jwt}
              </div>
            )}
            {mandate.claims && (
              <CodeBlock
                code={mandate.claims}
                title="Decoded Mandate Claims (RFC 9901)"
                maxHeight="max-h-56"
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
};
