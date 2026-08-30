import React, { useCallback, useEffect, useState } from 'react';
import { api, BackendUnavailableError } from '../../services/api';
import {
  AgentPurchase,
  PurchaseTrace,
  TraceMandate,
  AgentPurchaseStatus,
} from '../../types';
import {
  Receipt as ReceiptIcon,
  Search,
  RefreshCw,
  Loader2,
  WifiOff,
  AlertTriangle,
  Link2,
  GitBranch,
  ShieldCheck,
  ShieldAlert,
  FileSignature,
  Terminal,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import { HashBadge } from '../common/HashBadge';
import { CodeBlock } from '../common/CodeBlock';

// Sourced live from GET /api/agent/purchases and GET /api/agent/purchases/{id}/trace
// (src/api/routers/agent.py). Evidence-critical: NEVER falls back to mockEngine. A
// purchase can debit more than one mandate — a sticky over-limit approval issues a
// one-shot child mandate carrying parent_jti, and settlement walks the whole ancestry,
// debiting the child AND every ancestor. That's what stops an approval from minting new
// spending power, so the trace panel renders the mandate chain child-first as a
// hierarchy, not a flat table, and calls out any ancestor whose debited amount exceeds
// its own max_per_txn — that's the guarantee working, not an error.

const badgeVariantForStatus = (status: AgentPurchaseStatus) => {
  switch (status) {
    case 'captured':
      return 'emerald' as const;
    case 'rejected':
      return 'rose' as const;
    case 'compensated':
      return 'amber' as const;
    case 'awaiting_escalation':
      return 'amber' as const;
    case 'charging':
      return 'cyan' as const;
    case 'pending':
    default:
      return 'slate' as const;
  }
};

const badgeVariantForMandateStatus = (status?: string) => {
  switch (status) {
    case 'active':
      return 'emerald' as const;
    case 'exhausted':
      return 'amber' as const;
    case 'revoked':
      return 'rose' as const;
    case 'expired':
      return 'rose' as const;
    case 'suspended':
      return 'amber' as const;
    case 'draft':
    default:
      return 'slate' as const;
  }
};

// Display-only thousands separator that never touches precision: string in, string out.
function formatAmount(amount: string): string {
  const [whole, frac] = amount.split('.');
  const negative = whole.startsWith('-');
  const digits = negative ? whole.slice(1) : whole;
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return (negative ? '-' : '') + grouped + (frac !== undefined ? '.' + frac : '');
}

function shortId(id: string, lead = 12): string {
  return id.length > lead + 3 ? `${id.slice(0, lead)}…` : id;
}

export const TransactionsView: React.FC = () => {
  const [purchases, setPurchases] = useState<AgentPurchase[]>([]);
  const [listStatus, setListStatus] = useState<'idle' | 'loading' | 'ok' | 'error'>('idle');
  const [listError, setListError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [trace, setTrace] = useState<PurchaseTrace | null>(null);
  const [traceStatus, setTraceStatus] = useState<'idle' | 'loading' | 'ok' | 'not_found' | 'error'>('idle');
  const [traceError, setTraceError] = useState<string | null>(null);

  const [expandedEventSeq, setExpandedEventSeq] = useState<number | null>(null);
  const [signatureExpanded, setSignatureExpanded] = useState(false);

  const fetchList = useCallback(async () => {
    setListStatus('loading');
    setListError(null);
    try {
      const result = await api.getAgentPurchases({ limit: 50 });
      setPurchases(result);
      setListStatus('ok');
    } catch (err) {
      setListStatus('error');
      setPurchases([]);
      setListError(
        err instanceof BackendUnavailableError
          ? err.message
          : 'Backend unreachable. Start the stack with `docker compose up` and try again.'
      );
    }
  }, []);

  const fetchTrace = useCallback(async (purchaseId: string) => {
    setSelectedId(purchaseId);
    setTraceStatus('loading');
    setTraceError(null);
    setExpandedEventSeq(null);
    setSignatureExpanded(false);
    try {
      const result = await api.getPurchaseTrace(purchaseId);
      if (result === null) {
        setTraceStatus('not_found');
        setTrace(null);
      } else {
        setTraceStatus('ok');
        setTrace(result);
      }
    } catch (err) {
      setTraceStatus('error');
      setTrace(null);
      setTraceError(
        err instanceof BackendUnavailableError
          ? err.message
          : 'Backend unreachable. Start the stack with `docker compose up` and try again.'
      );
    }
  }, []);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  const filteredPurchases = purchases.filter((p) => {
    const term = searchTerm.toLowerCase();
    if (!term) return true;
    return (
      p.purchase_id.toLowerCase().includes(term) ||
      p.mandate_jti.toLowerCase().includes(term) ||
      p.status.toLowerCase().includes(term) ||
      (p.reason_code || '').toLowerCase().includes(term)
    );
  });

  // Chain-first ordering (depth 0 = authorising mandate that debits itself, higher depth
  // = ancestors further up the chain). The API already returns them in this order, but
  // sort defensively since the ordering IS the hierarchy this view exists to show.
  const orderedMandates = trace ? [...trace.mandates].sort((a, b) => a.depth - b.depth) : [];

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-400">
            <ReceiptIcon className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">Transactions & Mandate Trace</h3>
              <Badge variant="cyan" size="sm">
                GET /agent/purchases
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Every mandate a purchase was debited against — child mandate first, ancestors below
            </p>
          </div>
        </div>

        <button
          onClick={fetchList}
          disabled={listStatus === 'loading'}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {listStatus === 'loading' ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <RefreshCw className="w-3.5 h-3.5" />
          )}
          Refresh
        </button>
      </div>

      {listStatus === 'error' && listError && (
        <div className="p-3 rounded-xl border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs font-mono flex items-center gap-2">
          <WifiOff className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{listError}</span>
        </div>
      )}

      {/* Search */}
      <div className="relative w-full sm:w-80">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
        <input
          type="text"
          placeholder="Search purchase, mandate, status, reason..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="w-full pl-9 pr-3 py-1.5 rounded-xl bg-[#0e131d] border border-slate-800 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500 font-mono"
        />
      </div>

      {/* Transaction list */}
      <div className="overflow-x-auto rounded-xl border border-slate-800 bg-[#0c1018]">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr className="bg-[#111724] border-b border-slate-800 text-slate-400 font-mono uppercase text-[10px] tracking-wider">
              <th className="py-2.5 px-3">Purchase</th>
              <th className="py-2.5 px-3">Status</th>
              <th className="py-2.5 px-3">Reason</th>
              <th className="py-2.5 px-3 text-right">Amount</th>
              <th className="py-2.5 px-3">Mandate</th>
              <th className="py-2.5 px-3">Chain</th>
              <th className="py-2.5 px-3 text-right">Time</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 font-mono">
            {filteredPurchases.map((p) => {
              const isSelected = selectedId === p.purchase_id;
              const sticky = p.mandate_depth > 1;
              return (
                <tr
                  key={p.purchase_id}
                  onClick={() => fetchTrace(p.purchase_id)}
                  className={`hover:bg-slate-800/40 transition-colors cursor-pointer ${
                    isSelected ? 'bg-cyan-950/20' : sticky ? 'bg-amber-950/10' : ''
                  }`}
                >
                  <td className="py-2.5 px-3 text-slate-200" title={p.purchase_id}>
                    {shortId(p.purchase_id)}
                  </td>
                  <td className="py-2.5 px-3">
                    <Badge variant={badgeVariantForStatus(p.status)} size="sm">
                      {p.status}
                    </Badge>
                  </td>
                  <td className="py-2.5 px-3 text-[11px] text-slate-400">{p.reason_code || '—'}</td>
                  <td className="py-2.5 px-3 text-right text-slate-200">
                    {formatAmount(p.amount)} <span className="text-slate-500">{p.currency}</span>
                  </td>
                  <td className="py-2.5 px-3 text-slate-400" title={p.mandate_jti}>
                    {shortId(p.mandate_jti, 10)}
                  </td>
                  <td className="py-2.5 px-3">
                    {sticky ? (
                      <Badge variant="amber" size="sm" pulse>
                        <GitBranch className="w-3 h-3" />
                        {p.mandate_depth} mandates
                      </Badge>
                    ) : (
                      <span className="text-slate-600 text-[11px]">1 mandate</span>
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-right text-[11px] text-slate-400">
                    {new Date(p.created_at).toLocaleString()}
                  </td>
                </tr>
              );
            })}
            {listStatus === 'ok' && filteredPurchases.length === 0 && (
              <tr>
                <td colSpan={7} className="py-8 text-center text-slate-500 text-xs">
                  No purchases match this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Trace panel */}
      {selectedId && (
        <div className="pt-2 border-t border-slate-800/80 space-y-4">
          {traceStatus === 'loading' && (
            <div className="p-6 flex items-center justify-center gap-2 text-slate-400 text-xs">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading trace for {shortId(selectedId)}…
            </div>
          )}

          {traceStatus === 'error' && traceError && (
            <div className="p-3 rounded-xl border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs font-mono flex items-center gap-2">
              <WifiOff className="w-4 h-4 text-rose-400 shrink-0" />
              <span>{traceError}</span>
            </div>
          )}

          {traceStatus === 'not_found' && (
            <div className="p-3 rounded-xl border border-slate-700 bg-slate-900/60 text-slate-300 text-xs font-mono flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-slate-400 shrink-0" />
              <span>
                No trace exists on the backend for purchase{' '}
                <strong className="text-slate-100">{selectedId}</strong>.
              </span>
            </div>
          )}

          {traceStatus === 'ok' && trace && (
            <>
              <div className="flex items-center gap-2">
                <h4 className="text-sm font-bold font-display text-white">
                  Mandate Chain — {trace.purchase_id}
                </h4>
                <Badge variant={badgeVariantForStatus(trace.status)} size="sm">
                  {trace.status}
                </Badge>
                <span className="text-xs text-slate-400 font-mono">
                  {formatAmount(trace.amount)} debited
                </span>
              </div>

              {/* Mandate ladder — child (depth 0, authorising) first, ancestors below */}
              <div className="space-y-0">
                {orderedMandates.map((m, idx) => (
                  <MandateRung
                    key={m.jti}
                    mandate={m}
                    isLast={idx === orderedMandates.length - 1}
                  />
                ))}
              </div>

              {/* Signed intent (compact) */}
              {trace.intent && (
                <div className="p-3.5 rounded-xl bg-[#0e131d] border border-slate-800 space-y-2.5">
                  <div className="flex items-center gap-2 text-indigo-400 font-semibold text-xs">
                    <FileSignature className="w-4 h-4" />
                    <span>Signed Intent</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px] font-mono text-slate-300">
                    <div>
                      <span className="text-slate-500">jti: </span>
                      {trace.intent.jti}
                    </div>
                    <div>
                      <span className="text-slate-500">agent_id: </span>
                      {trace.intent.agent_id}
                    </div>
                    <div>
                      <span className="text-slate-500">nonce: </span>
                      {shortId(trace.intent.nonce, 16)}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <HashBadge hash={trace.intent.signature} variant="indigo" label="JWS" lead={10} trail={8} />
                    <button
                      onClick={() => setSignatureExpanded((v) => !v)}
                      className="text-[11px] text-slate-400 hover:text-slate-200 underline decoration-dotted cursor-pointer"
                    >
                      {signatureExpanded ? 'hide full signature' : 'view full signature'}
                    </button>
                  </div>
                  {signatureExpanded && (
                    <CodeBlock code={trace.intent.signature} language="jws" maxHeight="max-h-24" />
                  )}
                </div>
              )}

              {/* Chain events */}
              {trace.events.length > 0 && (
                <div className="overflow-x-auto rounded-xl border border-slate-800 bg-[#0c1018]">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead>
                      <tr className="bg-[#111724] border-b border-slate-800 text-slate-400 font-mono uppercase text-[10px] tracking-wider">
                        <th className="py-2 px-3 w-14">Seq</th>
                        <th className="py-2 px-3">Type</th>
                        <th className="py-2 px-3">Decision / Reason</th>
                        <th className="py-2 px-3 text-right">Time</th>
                        <th className="py-2 px-2 w-8"></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60 font-mono">
                      {trace.events.map((evt) => {
                        const isExpanded = expandedEventSeq === evt.seq;
                        const payloadReason =
                          (evt.payload?.reason_code as string | undefined) ||
                          (evt.payload?.decision as string | undefined) ||
                          (evt.payload?.reason as string | undefined);
                        return (
                          <React.Fragment key={evt.seq}>
                            <tr
                              onClick={() => setExpandedEventSeq(isExpanded ? null : evt.seq)}
                              className={`hover:bg-slate-800/40 transition-colors cursor-pointer ${
                                isExpanded ? 'bg-indigo-950/20' : ''
                              }`}
                            >
                              <td className="py-2 px-3 text-slate-300">#{evt.seq}</td>
                              <td className="py-2 px-3">
                                <Badge variant="indigo" size="sm">
                                  {evt.type}
                                </Badge>
                              </td>
                              <td className="py-2 px-3 text-[11px] text-slate-400">{payloadReason || '—'}</td>
                              <td className="py-2 px-3 text-right text-[11px] text-slate-400">
                                {new Date(evt.created_at).toLocaleTimeString()}
                              </td>
                              <td className="py-2 px-2 text-right text-slate-500">
                                {isExpanded ? (
                                  <ChevronDown className="w-4 h-4" />
                                ) : (
                                  <ChevronRight className="w-4 h-4" />
                                )}
                              </td>
                            </tr>
                            {isExpanded && (
                              <tr className="bg-[#080b11]/90">
                                <td colSpan={5} className="p-4 border-b border-indigo-500/20">
                                  <div className="space-y-2">
                                    <div className="flex items-center gap-1.5 text-[11px] text-slate-400 font-sans font-semibold">
                                      <Terminal className="w-3.5 h-3.5 text-indigo-400" /> Event Payload
                                    </div>
                                    <CodeBlock code={evt.payload} title={`Event #${evt.seq} · ${evt.type}`} maxHeight="max-h-48" />
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
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};

const MandateRung: React.FC<{ mandate: TraceMandate; isLast: boolean }> = ({ mandate, isLast }) => {
  if (mandate.missing) {
    return (
      <div className="flex gap-3">
        <div className="flex flex-col items-center w-6 shrink-0">
          <div className="w-2.5 h-2.5 rounded-full bg-slate-700 mt-1.5" />
          {!isLast && <div className="w-px flex-1 bg-slate-800 my-1" />}
        </div>
        <div className="flex-1 p-3 mb-2 rounded-xl border border-dashed border-slate-700 bg-slate-900/40 text-xs text-slate-500 font-mono">
          depth {mandate.depth} — mandate <span className="text-slate-400">{mandate.jti}</span> is missing from the
          ledger. Ancestry gap, not fabricated.
        </div>
      </div>
    );
  }

  const overLimit =
    mandate.debited !== undefined &&
    mandate.limits?.max_per_txn !== undefined &&
    parseFloat(mandate.debited) > parseFloat(mandate.limits.max_per_txn);

  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center w-6 shrink-0">
        <div
          className={`w-2.5 h-2.5 rounded-full mt-1.5 ${
            mandate.role === 'authorising' ? 'bg-cyan-400' : 'bg-purple-400'
          }`}
        />
        {!isLast && <div className="w-px flex-1 bg-slate-800 my-1" />}
      </div>

      <div
        className={`flex-1 p-4 mb-3 rounded-xl border space-y-3 ${
          overLimit ? 'border-amber-500/50 bg-amber-950/10' : 'border-slate-800 bg-[#0e131d]'
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant={mandate.role === 'authorising' ? 'cyan' : 'purple'} size="sm">
              {mandate.role === 'authorising' ? 'authorising' : 'ancestor'}
            </Badge>
            <span className="text-[10px] uppercase text-slate-500 font-mono">depth {mandate.depth}</span>
            <Badge variant={badgeVariantForMandateStatus(mandate.status)} size="sm">
              {mandate.status}
            </Badge>
            <HashBadge hash={mandate.jti} variant="mono" lead={10} trail={4} />
          </div>
          {mandate.parent_jti && (
            <span className="flex items-center gap-1.5 text-[11px] text-slate-500 font-mono">
              <Link2 className="w-3 h-3" /> derived from
              <HashBadge hash={mandate.parent_jti} variant="mono" lead={8} trail={4} />
            </span>
          )}
        </div>

        {mandate.limits?.signed_with && (
          <div className="text-[11px] text-slate-400 font-mono">
            signed_with: <span className="text-slate-200">{mandate.limits.signed_with}</span>
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-[11px] font-mono">
          <Stat label="max_per_txn" value={mandate.limits ? formatAmount(mandate.limits.max_per_txn) : '—'} />
          <Stat label="total_budget" value={mandate.limits ? formatAmount(mandate.limits.total_budget) : '—'} />
          <Stat
            label="max_txn"
            value={mandate.limits ? `${mandate.limits.max_txn.count}/${mandate.limits.max_txn.period}` : '—'}
          />
          <Stat label="txn_count" value={mandate.txn_count !== undefined ? String(mandate.txn_count) : '—'} />
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5 text-[11px] font-mono pt-1 border-t border-slate-800/70">
          <Stat
            label="debited this purchase"
            value={mandate.debited !== undefined ? formatAmount(mandate.debited) : '—'}
            emphasis={overLimit ? 'amber' : undefined}
          />
          <Stat label="spent_total" value={mandate.spent_total !== undefined ? formatAmount(mandate.spent_total) : '—'} />
          <Stat
            label="reserved_amount"
            value={mandate.reserved_amount !== undefined ? formatAmount(mandate.reserved_amount) : '—'}
          />
        </div>

        {overLimit && (
          <div className="flex items-start gap-2 p-2.5 rounded-lg bg-amber-950/30 border border-amber-500/40 text-amber-300 text-[11px] font-mono">
            <ShieldCheck className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <span>
              This ancestor's own max_per_txn is {formatAmount(mandate.limits!.max_per_txn)}, yet it absorbed the
              full {formatAmount(mandate.debited!)} debit — the child mandate's sticky approval walked the ancestry
              and drew it down instead of minting new spending power. This is the guarantee working, not an error.
            </span>
          </div>
        )}
      </div>
    </div>
  );
};

const Stat: React.FC<{ label: string; value: string; emphasis?: 'amber' }> = ({ label, value, emphasis }) => (
  <div>
    <div className="text-[10px] uppercase text-slate-500">{label}</div>
    <div className={emphasis === 'amber' ? 'text-amber-300 font-bold flex items-center gap-1' : 'text-slate-200'}>
      {emphasis === 'amber' && <ShieldAlert className="w-3 h-3 text-amber-400" />}
      {value}
    </div>
  </div>
);
