import React, { useState } from 'react';
import { useApp } from '../../context/AppContext';
import {
  ShieldCheck,
  ShieldAlert,
  Search,
  Filter,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  Terminal,
  Lock,
  Layers,
  AlertTriangle,
  WifiOff,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import { HashBadge } from '../common/HashBadge';
import { CodeBlock } from '../common/CodeBlock';

export const HashChainLedgerTable: React.FC = () => {
  const { auditEvents, verifyChain, verifyResult, auditBackendError, isLiveBackend } = useApp();
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedType, setSelectedType] = useState<string>('all');
  const [expandedSeq, setExpandedSeq] = useState<number | null>(null);
  const [isVerifying, setIsVerifying] = useState(false);

  const eventTypes = Array.from(new Set(auditEvents.map((e) => e.type)));

  const filteredEvents = auditEvents.filter((evt) => {
    const matchesSearch =
      evt.type.toLowerCase().includes(searchTerm.toLowerCase()) ||
      evt.mandate_id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      evt.hash.toLowerCase().includes(searchTerm.toLowerCase()) ||
      JSON.stringify(evt.payload).toLowerCase().includes(searchTerm.toLowerCase());

    const matchesType = selectedType === 'all' || evt.type === selectedType;
    return matchesSearch && matchesType;
  });

  const handleVerify = async () => {
    setIsVerifying(true);
    await verifyChain();
    setIsVerifying(false);
  };

  const handleExportJSON = () => {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(auditEvents, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', `aval_audit_trail_${Date.now()}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  const getTypeBadgeVariant = (type: string) => {
    if (type.includes('revoked') || type.includes('rejected') || type.includes('expired')) return 'rose' as const;
    if (type.includes('created') || type.includes('activated') || type.includes('captured')) return 'emerald' as const;
    if (type.includes('escalated')) return 'amber' as const;
    if (type.includes('checkpoint')) return 'purple' as const;
    return 'indigo' as const;
  };

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      {/* Header with Verify and Export */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
            <Layers className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                Append-Only Hash-Chained Audit Ledger
              </h3>
              <Badge variant="indigo" size="sm">
                {auditEvents.length} Events
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              H(Seq : Prev_Hash : Type : Canonical_JSON(Payload)) · Continuous Merkle Chaining
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 w-full sm:w-auto">
          <button
            onClick={handleVerify}
            disabled={isVerifying}
            className="flex-1 sm:flex-none flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-950/40 transition-all cursor-pointer disabled:opacity-50"
          >
            {isVerifying ? (
              <>
                <ShieldCheck className="w-4 h-4 animate-spin" /> Verifying Hashes...
              </>
            ) : verifyResult?.valid === false ? (
              <>
                <ShieldAlert className="w-4 h-4 text-rose-300" /> Tamper Detected
              </>
            ) : (
              <>
                <ShieldCheck className="w-4 h-4 text-emerald-300" /> verify_all() Chain
              </>
            )}
          </button>

          <button
            onClick={handleExportJSON}
            className="p-2 rounded-xl border border-slate-700 bg-slate-800/50 hover:bg-slate-700 text-slate-300 text-xs flex items-center gap-1.5 transition-colors"
            title="Export full audit trail as JSON"
          >
            <Download className="w-4 h-4" />
            <span className="hidden md:inline">Export</span>
          </button>
        </div>
      </div>

      {/* Simulated ledger notice — this table is always sourced from the local demo
          engine, not the live backend, until "verify_all() Chain" succeeds against it. */}
      {!isLiveBackend && !auditBackendError && (
        <div className="p-3 rounded-xl border border-amber-500/40 bg-amber-950/30 text-amber-300 text-xs font-mono flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          <span>
            SIMULATED LEDGER — no live backend detected. These blocks are demo data from the local sandbox, not
            backend-verified evidence.
          </span>
        </div>
      )}

      {/* Audit backend unreachable — never render a chain/verdict pill in this state */}
      {auditBackendError && (
        <div className="p-3 rounded-xl border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs font-mono flex items-center gap-2">
          <WifiOff className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{auditBackendError}</span>
        </div>
      )}

      {/* Verification Status Pill */}
      {!auditBackendError && verifyResult && (
        <div
          className={`p-3 rounded-xl border text-xs font-mono flex items-center justify-between ${
            verifyResult.valid
              ? 'bg-emerald-950/30 border-emerald-500/40 text-emerald-300'
              : 'bg-rose-950/30 border-rose-500/40 text-rose-300'
          }`}
        >
          <div className="flex items-center gap-2">
            {verifyResult.valid ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            ) : (
              <ShieldAlert className="w-4 h-4 text-rose-400" />
            )}
            <span>
              {verifyResult.valid
                ? `Chain Verified 100%: ${verifyResult.events_checked} blocks evaluated. Genesis-to-tip integrity intact.`
                : verifyResult.error}
            </span>
          </div>
          {verifyResult.last_root && (
            <div className="hidden lg:flex items-center gap-2 text-[11px]">
              <span className="text-slate-400">Signed Root:</span>
              <HashBadge hash={verifyResult.last_root} variant="emerald" lead={6} trail={4} />
            </div>
          )}
        </div>
      )}

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
        <div className="relative w-full sm:w-72">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            type="text"
            placeholder="Search seq, hash, type, payload..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 rounded-xl bg-[#0e131d] border border-slate-800 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          <Filter className="w-3.5 h-3.5 text-slate-400" />
          <select
            value={selectedType}
            onChange={(e) => setSelectedType(e.target.value)}
            className="w-full sm:w-auto px-3 py-1.5 rounded-xl bg-[#0e131d] border border-slate-800 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
          >
            <option value="all">All Event Types ({auditEvents.length})</option>
            {eventTypes.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Ledger Table */}
      <div className="overflow-x-auto rounded-xl border border-slate-800 bg-[#0c1018]">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr className="bg-[#111724] border-b border-slate-800 text-slate-400 font-mono uppercase text-[10px] tracking-wider">
              <th className="py-2.5 px-3 w-16">Seq</th>
              <th className="py-2.5 px-3 w-40">Event Type</th>
              <th className="py-2.5 px-3">Prev Hash</th>
              <th className="py-2.5 px-3">Current Hash</th>
              <th className="py-2.5 px-3">Root Sig</th>
              <th className="py-2.5 px-3 w-28 text-right">Timestamp</th>
              <th className="py-2.5 px-2 w-10"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 font-mono">
            {filteredEvents.map((evt) => {
              const isExpanded = expandedSeq === evt.seq;
              const isTampered = evt.tampered;

              return (
                <React.Fragment key={evt.seq}>
                  <tr
                    onClick={() => setExpandedSeq(isExpanded ? null : evt.seq)}
                    className={`hover:bg-slate-800/40 transition-colors cursor-pointer ${
                      isTampered
                        ? 'bg-rose-950/30 text-rose-200'
                        : isExpanded
                        ? 'bg-indigo-950/20'
                        : ''
                    }`}
                  >
                    <td className="py-2.5 px-3 font-bold text-slate-300">
                      <span className="flex items-center gap-1">
                        #{evt.seq}
                        {isTampered && <span className="w-1.5 h-1.5 rounded-full bg-rose-500 animate-ping" />}
                      </span>
                    </td>
                    <td className="py-2.5 px-3">
                      <Badge variant={getTypeBadgeVariant(evt.type)} size="sm">
                        {evt.type}
                      </Badge>
                    </td>
                    <td className="py-2.5 px-3">
                      <HashBadge
                        hash={evt.prev_hash}
                        variant={evt.prev_hash.startsWith('000000') ? 'mono' : 'cyan'}
                        lead={6}
                        trail={4}
                      />
                    </td>
                    <td className="py-2.5 px-3">
                      <HashBadge
                        hash={evt.hash}
                        variant={isTampered ? 'rose' : 'emerald'}
                        lead={6}
                        trail={4}
                      />
                    </td>
                    <td className="py-2.5 px-3">
                      {evt.root_sig ? (
                        <span className="flex items-center gap-1 text-[11px] text-purple-400 font-medium">
                          <Lock className="w-3 h-3 text-purple-400" /> KMS Signed
                        </span>
                      ) : (
                        <span className="text-slate-600 text-[11px]">—</span>
                      )}
                    </td>
                    <td className="py-2.5 px-3 text-right text-[11px] text-slate-400">
                      {new Date(evt.created_at).toLocaleTimeString()}
                    </td>
                    <td className="py-2.5 px-2 text-right text-slate-500">
                      {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                    </td>
                  </tr>

                  {/* Expanded Payload View */}
                  {isExpanded && (
                    <tr className="bg-[#080b11]/90">
                      <td colSpan={7} className="p-4 border-b border-indigo-500/20">
                        <div className="space-y-3">
                          <div className="flex items-center justify-between text-[11px] text-slate-400">
                            <span className="flex items-center gap-1.5 font-sans font-semibold text-slate-200">
                              <Terminal className="w-3.5 h-3.5 text-indigo-400" /> Canonical JSON Payload (RFC 8785 / JCS)
                            </span>
                            <span>Mandate: <strong className="text-slate-200">{evt.mandate_id}</strong></span>
                          </div>
                          <CodeBlock
                            code={evt.payload}
                            title={`Block #${evt.seq} · ${evt.type}`}
                            maxHeight="max-h-56"
                          />
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
