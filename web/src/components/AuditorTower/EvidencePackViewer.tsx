import React, { useCallback, useEffect, useState } from 'react';
import { useApp } from '../../context/AppContext';
import { api, BackendUnavailableError } from '../../services/api';
import { EvidencePack } from '../../types';
import {
  FileCheck,
  Download,
  Search,
  ShieldCheck,
  ShieldAlert,
  WifiOff,
  AlertTriangle,
  Loader2,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import { HashBadge } from '../common/HashBadge';
import { CodeBlock } from '../common/CodeBlock';

type FetchStatus = 'idle' | 'loading' | 'ok' | 'not_found' | 'error';

// Sourced live from GET /api/purchases/{purchase_id}/evidence-pack (src/api/routers/
// evidence.py, backed by EvidencePack.to_dict()). This view never invents cryptographic
// material: no field is shown unless it came back from the backend, and the download
// button is disabled whenever there is nothing real to hand out.
export const EvidencePackViewer: React.FC = () => {
  const { isLiveBackend, receipts } = useApp();
  const [purchaseId, setPurchaseId] = useState<string>(() => receipts[0]?.purchase_id || '');
  const [status, setStatus] = useState<FetchStatus>('idle');
  const [pack, setPack] = useState<EvidencePack | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const fetchPack = useCallback(async (id: string) => {
    if (!isLiveBackend) {
      setStatus('idle');
      setPack(null);
      return;
    }
    if (!id.trim()) {
      setStatus('idle');
      setPack(null);
      return;
    }
    setStatus('loading');
    setErrorMessage(null);
    try {
      const result = await api.getEvidencePack(id.trim());
      if (result === null) {
        setStatus('not_found');
        setPack(null);
      } else {
        setStatus('ok');
        setPack(result);
      }
    } catch (err) {
      setStatus('error');
      setPack(null);
      setErrorMessage(
        err instanceof BackendUnavailableError
          ? err.message
          : 'Backend unreachable. Start the stack with `docker compose up` and try again.'
      );
    }
  }, [isLiveBackend]);

  // Re-fetch (or clear) whenever backend reachability flips — never leave a pack from a
  // previous live session on screen once the backend that vouched for it is gone.
  useEffect(() => {
    fetchPack(purchaseId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLiveBackend]);

  const handleDownload = () => {
    if (status !== 'ok' || !pack) return;
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(pack, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', `aval_evidence_pack_${pack.purchase_id}_${Date.now()}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  const canDownload = status === 'ok' && pack !== null;

  return (
    <div className="rounded-2xl glass-panel p-5 sm:p-6 border border-slate-800/80 space-y-5">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-purple-500/10 border border-purple-500/30 text-purple-400">
            <FileCheck className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold font-display text-white">
                Cryptographic Evidence Pack (Live)
              </h3>
              <Badge variant="purple" size="sm">
                GET /purchases/:id/evidence-pack
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Mandate claims · Intent · Decision · Receipt · Ledger slice · Chain verdict · Root checkpoint
            </p>
          </div>
        </div>

        <button
          onClick={handleDownload}
          disabled={!canDownload}
          title={canDownload ? 'Download the real evidence pack as JSON' : 'No verified pack to download'}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold shadow-lg shadow-purple-950/40 transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-purple-600"
        >
          <Download className="w-4 h-4" /> Download Evidence Pack
        </button>
      </div>

      {/* Purchase lookup */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2.5">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            type="text"
            value={purchaseId}
            onChange={(e) => setPurchaseId(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && fetchPack(purchaseId)}
            placeholder="purchase_id (e.g. from a captured receipt)..."
            className="w-full pl-9 pr-3 py-2 rounded-xl bg-[#0e131d] border border-slate-800 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-purple-500 font-mono"
          />
        </div>
        <button
          onClick={() => fetchPack(purchaseId)}
          disabled={!isLiveBackend || status === 'loading'}
          className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {status === 'loading' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileCheck className="w-3.5 h-3.5" />}
          Fetch from Backend
        </button>
      </div>

      {/* Simulated mode: refuse to assemble a pack from anything but the backend */}
      {!isLiveBackend && (
        <div className="p-3 rounded-xl border border-amber-500/40 bg-amber-950/30 text-amber-300 text-xs font-mono flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          <span>
            SIMULATED MODE — no live backend detected. An evidence pack is real cryptographic proof; the local
            sandbox cannot produce one, so this view stays empty and the download stays disabled until the backend
            is reachable.
          </span>
        </div>
      )}

      {isLiveBackend && status === 'error' && errorMessage && (
        <div className="p-3 rounded-xl border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs font-mono flex items-center gap-2">
          <WifiOff className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {isLiveBackend && status === 'not_found' && (
        <div className="p-3 rounded-xl border border-slate-700 bg-slate-900/60 text-slate-300 text-xs font-mono flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-slate-400 shrink-0" />
          <span>
            No evidence pack exists on the backend for purchase <strong className="text-slate-100">{purchaseId || '(empty)'}</strong>.
            Nothing is fabricated in its place — run a purchase against the live backend first.
          </span>
        </div>
      )}

      {isLiveBackend && status === 'ok' && pack && (
        <>
          <div
            className={`p-3 rounded-xl border text-xs font-mono flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 ${
              pack.integrity === 'ok'
                ? 'bg-emerald-950/30 border-emerald-500/40 text-emerald-300'
                : 'bg-rose-950/30 border-rose-500/40 text-rose-300'
            }`}
          >
            <div className="flex items-center gap-2">
              {pack.integrity === 'ok' ? (
                <ShieldCheck className="w-4 h-4 text-emerald-400" />
              ) : (
                <ShieldAlert className="w-4 h-4 text-rose-400" />
              )}
              <span>
                Integrity: <strong>{pack.integrity.toUpperCase()}</strong>
                {pack.integrity === 'failed' && pack.failure_reasons.length > 0 && (
                  <span className="ml-2 text-rose-200">({pack.failure_reasons.join(', ')})</span>
                )}
              </span>
            </div>
            <HashBadge hash={pack.digest} variant={pack.integrity === 'ok' ? 'emerald' : 'rose'} label="DIGEST" lead={6} trail={4} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs font-mono">
            <div className="p-3.5 rounded-xl bg-[#0e131d] border border-slate-800 space-y-2">
              <div className="flex items-center gap-2 text-indigo-400 font-semibold">
                <FileCheck className="w-4 h-4" />
                <span>Ledger Slice</span>
              </div>
              <p className="text-slate-400 text-[11px]">
                {pack.ledger_events.length} event{pack.ledger_events.length === 1 ? '' : 's'} for mandate{' '}
                <span className="text-slate-200">{pack.mandate_jti}</span>
              </p>
            </div>

            <div className="p-3.5 rounded-xl bg-[#0e131d] border border-slate-800 space-y-2">
              <div className="flex items-center gap-2 text-cyan-400 font-semibold">
                {pack.chain?.ok ? <ShieldCheck className="w-4 h-4" /> : <ShieldAlert className="w-4 h-4" />}
                <span>Chain Verdict</span>
              </div>
              {pack.chain ? (
                <p className="text-slate-400 text-[11px]">
                  {pack.chain.ok
                    ? 'Ledger slice verified intact.'
                    : `Broken at seq #${pack.chain.first_bad_seq ?? '?'} — ${pack.chain.reason || 'unspecified'}`}
                </p>
              ) : (
                <p className="text-slate-500 text-[11px]">Not reported by backend.</p>
              )}
            </div>

            <div className="p-3.5 rounded-xl bg-[#0e131d] border border-slate-800 space-y-2">
              <div className="flex items-center gap-2 text-emerald-400 font-semibold">
                <ShieldCheck className="w-4 h-4" />
                <span>Root Checkpoint</span>
              </div>
              {pack.root_checkpoint ? (
                <p className="text-slate-400 text-[11px]">Present — see full pack below.</p>
              ) : (
                <p className="text-slate-500 text-[11px]">Unavailable — not yet witnessed. Not fabricated.</p>
              )}
            </div>
          </div>

          <CodeBlock
            code={pack}
            title={`Evidence Pack — purchase ${pack.purchase_id}`}
            maxHeight="max-h-72"
          />
        </>
      )}
    </div>
  );
};
