import React, { useState } from 'react';
import { Fingerprint, RefreshCw } from 'lucide-react';
import { Badge } from '../common/Badge';

interface PasskeyCeremonyProps {
  onRegistered?: (keyData: { credentialId: string; publicKey: string }) => void;
}

export const PasskeyCeremony: React.FC<PasskeyCeremonyProps> = ({ onRegistered }) => {
  const [isRegistering, setIsRegistering] = useState(false);
  const [isRegistered, setIsRegistered] = useState(true);
  const [credentialId, setCredentialId] = useState('cred_webauthn_marta_touchid_01J8');
  const [pubKey] = useState('O2aFRL2rOHJqLzp5B2N4dG_JkP1mR8sTuVwXyZaBcDe');

  const handleRegister = () => {
    setIsRegistering(true);
    setTimeout(() => {
      const newCred = `cred_${Date.now().toString(36)}`;
      setCredentialId(newCred);
      setIsRegistered(true);
      setIsRegistering(false);
      if (onRegistered) {
        onRegistered({ credentialId: newCred, publicKey: pubKey });
      }
    }, 600);
  };

  return (
    <div className="p-4 rounded-xl bg-[#0e131d] border border-slate-800 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-lg bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
            <Fingerprint className="w-4 h-4" />
          </div>
          <div>
            <h4 className="text-xs font-bold font-display text-white">
              WebAuthn Passkey (FIDO2 / Touch ID)
            </h4>
            <p className="text-[11px] text-slate-400">
              User Verification (UV=true) · RP ID: <span className="text-slate-300 font-mono">trytrust.app</span>
            </p>
          </div>
        </div>

        <Badge variant={isRegistered ? 'emerald' : 'slate'} size="sm">
          {isRegistered ? 'Key Active' : 'Unregistered'}
        </Badge>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] font-mono">
        <div className="p-2 rounded bg-[#141a27] border border-slate-800">
          <span className="text-slate-500 block text-[10px]">CREDENTIAL ID</span>
          <span className="text-slate-300 truncate block">{credentialId}</span>
        </div>
        <div className="p-2 rounded bg-[#141a27] border border-slate-800">
          <span className="text-slate-500 block text-[10px]">ALGORITHM</span>
          <span className="text-emerald-400 font-semibold">Ed25519 / OKP (RFC 8032)</span>
        </div>
      </div>

      <button
        onClick={handleRegister}
        disabled={isRegistering}
        className="w-full flex items-center justify-center gap-2 py-2 rounded-xl border border-indigo-500/40 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-300 text-xs font-semibold transition-colors cursor-pointer disabled:opacity-50"
      >
        {isRegistering ? (
          <>
            <RefreshCw className="w-3.5 h-3.5 animate-spin" /> Performing Biometric Ceremony...
          </>
        ) : (
          <>
            <Fingerprint className="w-3.5 h-3.5" /> Re-Register WebAuthn Passkey
          </>
        )}
      </button>
    </div>
  );
};
