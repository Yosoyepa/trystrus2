import React, { useState } from 'react';
import { HashChainLedgerTable } from './HashChainLedgerTable';
import { TamperMutationInjector } from './TamperMutationInjector';
import { EscalationsQueue } from './EscalationsQueue';
import { TelemetryDashboard } from './TelemetryDashboard';
import { EvidencePackViewer } from './EvidencePackViewer';
import { RedAlertBanner } from '../RedAlertBanner';
import { Layers, Bug, Clock, Activity, FileCheck } from 'lucide-react';
import { clsx } from 'clsx';

export const AuditorTower: React.FC = () => {
  const [subTab, setSubTab] = useState<'ledger' | 'tamper' | 'escalations' | 'telemetry' | 'evidence'>('ledger');

  const subTabs = [
    { id: 'ledger' as const, label: 'Hash-Chained Ledger', icon: Layers },
    { id: 'tamper' as const, label: 'Tamper Mutation Sandbox', icon: Bug },
    { id: 'escalations' as const, label: 'Escalations Queue', icon: Clock },
    { id: 'telemetry' as const, label: 'Telemetry & Concurrency', icon: Activity },
    { id: 'evidence' as const, label: 'Evidence Proof Pack', icon: FileCheck },
  ];

  return (
    <div className="space-y-6">
      {/* Red Alert Banner on top if tamper is triggered */}
      <RedAlertBanner />

      {/* Sub-navigation pills */}
      <div className="flex flex-wrap items-center gap-2 p-1.5 rounded-2xl bg-[#0e131d] border border-slate-800/80 w-fit">
        {subTabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = subTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setSubTab(tab.id)}
              className={clsx(
                'flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer',
                isActive
                  ? 'bg-indigo-600 text-white shadow-md shadow-indigo-950/50'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* Main View Area */}
      {subTab === 'ledger' && <HashChainLedgerTable />}
      {subTab === 'tamper' && <TamperMutationInjector />}
      {subTab === 'escalations' && <EscalationsQueue />}
      {subTab === 'telemetry' && <TelemetryDashboard />}
      {subTab === 'evidence' && <EvidencePackViewer />}
    </div>
  );
};
