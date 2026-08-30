import React, { useEffect } from 'react';
import { useApp } from '../context/AppContext';
import {
  ShieldCheck,
  UserCheck,
  Store,
  PlayCircle,
  Activity,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Cpu,
} from 'lucide-react';
import { clsx } from 'clsx';
import { Badge } from './common/Badge';

export const Header: React.FC = () => {
  const {
    activeTab,
    setActiveTab,
    isLiveBackend,
    checkBackendStatus,
    telemetry,
    activeMandate,
    escalations,
    redAlert,
  } = useApp();

  useEffect(() => {
    checkBackendStatus();
  }, []);

  const pendingEscalations = escalations.filter((e) => e.status === 'pending');

  const tabs = [
    {
      id: 'demo' as const,
      label: '1-Click Demo Runner',
      icon: PlayCircle,
      badge: '10 Scenarios',
      badgeVariant: 'indigo' as const,
    },
    {
      id: 'auditor' as const,
      label: 'Auditor Control Tower',
      icon: ShieldCheck,
      badge: redAlert ? 'TAMPER ALERT' : 'Ledger Verified',
      badgeVariant: redAlert ? ('rose' as const) : ('emerald' as const),
    },
    {
      id: 'buyer' as const,
      label: 'Buyer Console',
      icon: UserCheck,
      badge: activeMandate ? `$${activeMandate.spent}/$${activeMandate.limits.total_budget}` : 'Draft',
      badgeVariant: 'cyan' as const,
    },
    {
      id: 'merchant' as const,
      label: 'Merchant & Rail',
      icon: Store,
      badge: 'VuelaYa + Yuno AP2',
      badgeVariant: 'purple' as const,
    },
  ];

  return (
    <header className="sticky top-0 z-40 w-full border-b border-slate-800/80 bg-[#0a0d14]/90 backdrop-blur-xl">
      {/* Top micro bar with system health */}
      <div className="px-4 py-1.5 bg-[#0e131d] border-b border-slate-800/50 flex flex-wrap items-center justify-between text-[11px] font-mono text-slate-400">
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-slate-300 font-semibold">GATE:</span> Deterministic (No LLM in Pay Path)
          </span>
          <span className="hidden md:inline-flex text-slate-600">|</span>
          <span className="hidden md:inline-flex items-center gap-1">
            <Cpu className="w-3.5 h-3.5 text-indigo-400" />
            <span>KMS Root:</span> <span className="text-slate-300">EC_SIGN_ED25519</span>
          </span>
          <span className="hidden lg:inline-flex text-slate-600">|</span>
          <span className="hidden lg:inline-flex items-center gap-1">
            <span>CAS Locks:</span> <span className="text-emerald-400">{telemetry.active_advisory_locks.length} active</span>
          </span>
          <span className="hidden lg:inline-flex text-slate-600">|</span>
          <span className="hidden lg:inline-flex items-center gap-1">
            <span>Latency:</span> <span className="text-slate-300">{telemetry.avg_latency_ms}ms</span>
          </span>
        </div>

        <div className="flex items-center gap-3">
          {pendingEscalations.length > 0 && (
            <button
              onClick={() => setActiveTab('auditor')}
              className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse"
            >
              <AlertTriangle className="w-3 h-3 text-amber-400" />
              <span>{pendingEscalations.length} Pending Approval (120s)</span>
            </button>
          )}

          <div className="flex items-center gap-2">
            <button
              onClick={() => checkBackendStatus()}
              title="Check backend endpoints (:8001 / :8002 / :8003)"
              className="flex items-center gap-1.5 px-2 py-0.5 rounded border border-slate-700 bg-slate-800/60 hover:bg-slate-700 text-slate-300 transition-colors"
            >
              <RefreshCw className="w-3 h-3 text-slate-400" />
              {isLiveBackend ? (
                <span className="text-emerald-400 font-semibold flex items-center gap-1">
                  <CheckCircle2 className="w-3 h-3" /> Live Backend
                </span>
              ) : (
                <span className="text-cyan-400 font-semibold flex items-center gap-1">
                  <Activity className="w-3 h-3" /> Sim Engine
                </span>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Main Header bar */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Logo */}
          <div className="flex items-center gap-3">
            <div className="relative flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 p-0.5 shadow-lg shadow-indigo-500/30">
              <div className="w-full h-full bg-[#0a0d14] rounded-[10px] flex items-center justify-center">
                <ShieldCheck className="w-6 h-6 text-indigo-400" />
              </div>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold font-display tracking-tight text-white">
                  Aval <span className="text-indigo-400 text-sm font-mono font-normal">(TryTrust)</span>
                </h1>
                <Badge variant="indigo" size="sm">
                  v1.1 M2
                </Badge>
              </div>
              <p className="text-[11px] text-slate-400 hidden sm:block">
                Trust layer for AI agent purchases · SD-JWT + Deterministic Gate + Hash-Chained Audit
              </p>
            </div>
          </div>

          {/* Nav Tabs */}
          <nav className="flex items-center gap-1 sm:gap-2">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={clsx(
                    'relative flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs sm:text-sm font-medium transition-all cursor-pointer',
                    isActive
                      ? 'bg-indigo-600/20 text-white border border-indigo-500/50 shadow-lg shadow-indigo-500/20'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40 border border-transparent'
                  )}
                >
                  <Icon className={clsx('w-4 h-4', isActive ? 'text-indigo-400' : 'text-slate-400')} />
                  <span className="font-semibold">{tab.label}</span>
                  {tab.badge && (
                    <span className="hidden xl:inline-block">
                      <Badge variant={tab.badgeVariant} size="sm">
                        {tab.badge}
                      </Badge>
                    </span>
                  )}
                  {isActive && (
                    <span className="absolute -bottom-[1px] left-3 right-3 h-0.5 bg-gradient-to-r from-transparent via-indigo-400 to-transparent" />
                  )}
                </button>
              );
            })}
          </nav>
        </div>
      </div>
    </header>
  );
};
