import React, { useState } from 'react';
import { useApp } from '../../context/AppContext';
import { MandateCard } from './MandateCard';
import { MandateCreator } from './MandateCreator';
import { PasskeyCeremony } from './PasskeyCeremony';
import { AgentChat } from './AgentChat';
import { KillSwitchModal } from './KillSwitchModal';
import { Modal } from '../common/Modal';
import { Plus, UserCheck, Key } from 'lucide-react';
import { Badge } from '../common/Badge';

export const BuyerConsole: React.FC = () => {
  const { mandates, activeMandate, setActiveMandate } = useApp();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [revokeMandateId, setRevokeMandateId] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      {/* Top Banner with Passkey status and Create button */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 p-5 sm:p-6 rounded-2xl glass-panel border border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-400">
            <UserCheck className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold font-display text-white">
                Buyer & Agent Delegated Authority Console
              </h2>
              <Badge variant="cyan" size="sm">
                Human Owner: Marta
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              SD-JWT Mandate Authority · Bound Agent Public Keys · Instant Kill Switch
            </p>
          </div>
        </div>

        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white text-xs font-semibold shadow-lg shadow-indigo-950/50 transition-all cursor-pointer"
        >
          <Plus className="w-4 h-4" /> Create New Mandate
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Passkey & Active Mandate Card */}
        <div className="lg:col-span-5 space-y-6">
          <PasskeyCeremony />

          {/* Mandate selector if multiple */}
          {mandates.length > 1 && (
            <div className="p-3 rounded-xl bg-[#0e131d] border border-slate-800 flex items-center justify-between text-xs font-mono">
              <span className="text-slate-400">Select Mandate:</span>
              <select
                value={activeMandate?.jti || ''}
                onChange={(e) => {
                  const found = mandates.find((m) => m.jti === e.target.value);
                  if (found) setActiveMandate(found);
                }}
                className="px-2.5 py-1 rounded-lg bg-[#141a27] border border-slate-700 text-slate-200"
              >
                {mandates.map((m) => (
                  <option key={m.jti} value={m.jti}>
                    {m.jti} ({m.status.toUpperCase()})
                  </option>
                ))}
              </select>
            </div>
          )}

          {activeMandate ? (
            <MandateCard
              mandate={activeMandate}
              onRevokeClick={(id) => setRevokeMandateId(id)}
            />
          ) : (
            <div className="p-8 rounded-2xl glass-panel text-center text-slate-400 text-xs">
              No active mandate found. Click "Create New Mandate" above.
            </div>
          )}
        </div>

        {/* Right Column: AI Agent Interactive Chat */}
        <div className="lg:col-span-7">
          <AgentChat />
        </div>
      </div>

      {/* Create Mandate Modal */}
      <Modal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        title="Issue New SD-JWT Purchase Mandate"
        subtitle="Cryptographically binds agent public key and deterministic spending limits"
        icon={<Key className="w-6 h-6 text-indigo-400" />}
      >
        <MandateCreator onCreated={() => setShowCreateModal(false)} />
      </Modal>

      {/* Kill Switch Modal */}
      {revokeMandateId && (
        <KillSwitchModal
          isOpen={!!revokeMandateId}
          onClose={() => setRevokeMandateId(null)}
          mandateId={revokeMandateId}
        />
      )}
    </div>
  );
};
