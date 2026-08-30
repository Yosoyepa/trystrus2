import React from 'react';
import { useApp } from '../../context/AppContext';
import { FileCheck, Download, Key, Link as LinkIcon, Database } from 'lucide-react';
import { Badge } from '../common/Badge';
import { HashBadge } from '../common/HashBadge';
import { CodeBlock } from '../common/CodeBlock';

export const EvidencePackViewer: React.FC = () => {
  const { activeMandate, auditEvents } = useApp();

  const evidenceBundle = {
    mandate_jti: activeMandate?.jti || 'mdt_01J8Z9X2K3',
    version: '1.1.0',
    issuer_jwks_url: 'https://api.aval.example/.well-known/jwks.json',
    issuer_key: {
      kty: 'OKP',
      crv: 'Ed25519',
      kid: 'issuer-key-v1',
      x: '11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo',
    },
    agent_key_binding: {
      kty: 'OKP',
      crv: 'Ed25519',
      kid: 'agt_flights',
      x: 'O2aFRL2rOHJqLzp5B2N4dG_JkP1mR8sTuVwXyZaBcDe',
    },
    disclosures: [
      { claim: 'email', salt: 'salt1_9a8b', value: 'marta@example.com' },
      { claim: 'shipping_address', salt: 'salt2_4c5d', value: 'Calle 100 #15-20, Bogota' },
    ],
    cart_binding: {
      protocol: 'AP2 (Autonomous Payments Protocol)',
      checkout_hash: 'chk_ap2_hash_e3b0c44298fc1c149a',
      merchant_key_id: 'vuelaya-es256-key-v1',
    },
    audit_merkle_checkpoint: {
      events_covered: auditEvents.length,
      root_hash: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      kms_signature: 'sig_kms_ed25519_production_witness_root_9a8b7c6d5e4f',
      witness_uri: 'gs://aval-audit-witness-southamerica-east1/2026/08/root-checkpoint.json',
    },
  };

  const handleDownloadEnvelope = () => {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(evidenceBundle, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', `aval_evidence_envelope_${Date.now()}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

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
                Cryptographic Evidence Pack Assembly
              </h3>
              <Badge variant="purple" size="sm">
                Full Non-Repudiation
              </Badge>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              SD-JWT Proofs · JCS Detached JWS · AP2 Cart Hashes · KMS-Signed Checkpoints
            </p>
          </div>
        </div>

        <button
          onClick={handleDownloadEnvelope}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold shadow-lg shadow-purple-950/40 transition-all cursor-pointer"
        >
          <Download className="w-4 h-4" /> Download Proof Envelope
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs font-mono">
        <div className="p-3.5 rounded-xl bg-[#0e131d] border border-slate-800 space-y-2">
          <div className="flex items-center gap-2 text-indigo-400 font-semibold">
            <Key className="w-4 h-4" />
            <span>1. SD-JWT Issuer Check</span>
          </div>
          <p className="text-slate-400 text-[11px]">
            Ed25519 signature over selective disclosures verified against published JWKS.
          </p>
          <HashBadge hash={evidenceBundle.issuer_key.x} variant="indigo" label="PUBKEY" lead={6} trail={4} />
        </div>

        <div className="p-3.5 rounded-xl bg-[#0e131d] border border-slate-800 space-y-2">
          <div className="flex items-center gap-2 text-cyan-400 font-semibold">
            <LinkIcon className="w-4 h-4" />
            <span>2. AP2 Cart Hash Binding</span>
          </div>
          <p className="text-slate-400 text-[11px]">
            Cart payload cryptographically bound into intent via base64url(SHA256(checkout_jwt)).
          </p>
          <HashBadge hash={evidenceBundle.cart_binding.checkout_hash} variant="cyan" label="HASH" lead={6} trail={4} />
        </div>

        <div className="p-3.5 rounded-xl bg-[#0e131d] border border-slate-800 space-y-2">
          <div className="flex items-center gap-2 text-emerald-400 font-semibold">
            <Database className="w-4 h-4" />
            <span>3. KMS Root Checkpoint</span>
          </div>
          <p className="text-slate-400 text-[11px]">
            Signed with Cloud KMS EC_SIGN_ED25519 and witnessed to immutable GCS bucket.
          </p>
          <HashBadge hash={evidenceBundle.audit_merkle_checkpoint.root_hash} variant="emerald" label="ROOT" lead={6} trail={4} />
        </div>
      </div>

      <CodeBlock
        code={evidenceBundle}
        title="Complete Cryptographic Proof Bundle (Evidence Envelope)"
        maxHeight="max-h-72"
      />
    </div>
  );
};
