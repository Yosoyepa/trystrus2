import React, { useState } from 'react';
import { FlightCatalog } from './FlightCatalog';
import { CryptoPipelineVisualizer } from './CryptoPipelineVisualizer';
import { YunoRailSimulator } from './YunoRailSimulator';
import { DisputeAdjudication } from './DisputeAdjudication';
import { Store, Plane, ShieldCheck, CreditCard, Scale } from 'lucide-react';
import { clsx } from 'clsx';
import { Badge } from '../common/Badge';

export const MerchantConsole: React.FC = () => {
  const [subTab, setSubTab] = useState<'catalog' | 'pipeline' | 'rail' | 'disputes'>('catalog');

  const subTabs = [
    { id: 'catalog' as const, label: 'VuelaYa Flight Catalog', icon: Plane },
    { id: 'pipeline' as const, label: '7-Step Verification Pipeline', icon: ShieldCheck },
    { id: 'rail' as const, label: 'Yuno AP2 Simulated Rail', icon: CreditCard },
    { id: 'disputes' as const, label: 'Dispute Adjudication Engine', icon: Scale },
  ];

  return (
    <div className="space-y-6">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 p-5 sm:p-6 rounded-2xl glass-panel border border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-purple-500/10 border border-purple-500/30 text-purple-400">
            <Store className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold font-display text-white">
                Merchant Console & Payment Rail Operations
              </h2>
              <Badge variant="purple" size="sm">
                VuelaYa × Yuno AP2
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Pay-Time SD-JWT Verification · MCP Tools Server · AP2 Settlement Simulator
            </p>
          </div>
        </div>
      </div>

      {/* Sub-navigation tabs */}
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
                  ? 'bg-purple-600 text-white shadow-md shadow-purple-950/50'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* Main Subview */}
      {subTab === 'catalog' && <FlightCatalog />}
      {subTab === 'pipeline' && <CryptoPipelineVisualizer />}
      {subTab === 'rail' && <YunoRailSimulator />}
      {subTab === 'disputes' && <DisputeAdjudication />}
    </div>
  );
};
