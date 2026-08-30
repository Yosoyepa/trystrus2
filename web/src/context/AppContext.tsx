import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import {
  MandateView,
  AuditEvent,
  AuditVerifyResult,
  Escalation,
  Offer,
  Receipt,
  VaultToken,
  DisputeRecord,
  TelemetryState,
  ToastMessage,
  MandateClaims,
} from '../types';
import { engine } from '../services/mockEngine';
import { api } from '../services/api';

interface AppContextType {
  activeTab: 'auditor' | 'buyer' | 'merchant' | 'demo';
  setActiveTab: (tab: 'auditor' | 'buyer' | 'merchant' | 'demo') => void;
  isLiveBackend: boolean;
  setIsLiveBackend: (val: boolean) => void;
  checkBackendStatus: () => Promise<boolean>;

  // Mandates
  mandates: MandateView[];
  activeMandate: MandateView | null;
  setActiveMandate: (m: MandateView | null) => void;
  createMandate: (claims: Partial<MandateClaims>) => Promise<MandateView>;
  revokeMandate: (mandateId: string) => Promise<{ success: boolean; latency_ms: number }>;

  // Audit Ledger
  auditEvents: AuditEvent[];
  verifyResult: AuditVerifyResult | null;
  verifyChain: () => Promise<AuditVerifyResult>;
  tamperBlock: (seq: number, mutatedPayload: Record<string, unknown>) => void;
  restoreLedger: () => Promise<void>;
  redAlert: AuditVerifyResult | null;
  setRedAlert: (res: AuditVerifyResult | null) => void;

  // Escalations
  escalations: Escalation[];
  resolveEscalation: (id: string, decision: 'APPROVE' | 'REJECT', sticky?: boolean) => Promise<void>;

  // Merchant & Offers
  offers: Offer[];
  updateOfferPrice: (offerId: string, newAmount: string) => Promise<void>;
  receipts: Receipt[];
  vaultTokens: VaultToken[];
  disputes: DisputeRecord[];
  openDispute: (captureId: string, reason?: string) => void;
  submitDisputeEvidence: (disputeId: string) => void;
  adjudicateDispute: (disputeId: string, outcome: 'MERCHANT_WON' | 'BUYER_WON') => void;

  // Telemetry
  telemetry: TelemetryState;

  // Toast Notifications
  toasts: ToastMessage[];
  addToast: (type: ToastMessage['type'], title: string, message: string) => void;
  removeToast: (id: string) => void;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [activeTab, setActiveTab] = useState<'auditor' | 'buyer' | 'merchant' | 'demo'>('demo');
  const [isLiveBackend, setIsLiveBackend] = useState<boolean>(false);
  const [mandates, setMandates] = useState<MandateView[]>(engine.mandates);
  const [activeMandate, setActiveMandate] = useState<MandateView | null>(engine.mandates[0] || null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>(engine.auditEvents);
  const [verifyResult, setVerifyResult] = useState<AuditVerifyResult | null>(null);
  const [redAlert, setRedAlert] = useState<AuditVerifyResult | null>(null);
  const [escalations, setEscalations] = useState<Escalation[]>(engine.escalations);
  const [offers, setOffers] = useState<Offer[]>(engine.offers);
  const [receipts, setReceipts] = useState<Receipt[]>(engine.receipts);
  const [vaultTokens, setVaultTokens] = useState<VaultToken[]>(engine.vaultTokens);
  const [disputes, setDisputes] = useState<DisputeRecord[]>(engine.disputes);
  const [telemetry, setTelemetry] = useState<TelemetryState>(engine.telemetry);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  // Sync state when engine updates
  const syncWithEngine = useCallback(() => {
    setMandates([...engine.mandates]);
    if (!activeMandate && engine.mandates.length > 0) {
      setActiveMandate(engine.mandates[0]);
    } else if (activeMandate) {
      const updated = engine.mandates.find((m) => m.jti === activeMandate.jti);
      if (updated) setActiveMandate({ ...updated });
    }
    setAuditEvents([...engine.auditEvents]);
    setEscalations([...engine.escalations]);
    setOffers([...engine.offers]);
    setReceipts([...engine.receipts]);
    setVaultTokens([...engine.vaultTokens]);
    setDisputes([...engine.disputes]);
    setTelemetry({ ...engine.telemetry });
  }, [activeMandate]);

  useEffect(() => {
    const unsubscribe = engine.subscribe(syncWithEngine);
    return () => unsubscribe();
  }, [syncWithEngine]);

  const addToast = useCallback((type: ToastMessage['type'], title: string, message: string) => {
    const id = `toast_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`;
    const newToast: ToastMessage = { id, type, title, message, timestamp: Date.now() };
    setToasts((prev) => [...prev, newToast]);

    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const checkBackendStatus = async (): Promise<boolean> => {
    const isUp = await api.checkBackendHealth();
    setIsLiveBackend(isUp);
    api.setUseRealBackend(isUp);
    if (isUp) {
      addToast('success', 'Backend Connected', 'Live FastAPI backend on :8001 / :8002 / :8003 detected.');
    }
    return isUp;
  };

  const handleCreateMandate = async (claims: Partial<MandateClaims>): Promise<MandateView> => {
    const m = await api.createMandate(claims);
    setActiveMandate(m);
    addToast('success', 'Mandate Signed & Issued', `Mandate ${m.jti} issued with SD-JWT.`);
    return m;
  };

  const handleRevokeMandate = async (mandateId: string) => {
    const res = await api.revokeMandate(mandateId);
    if (res.success) {
      addToast('warning', 'Mandate Revoked (<2s)', `Revoked in ${res.latency_ms}ms. Rail token deleted.`);
    }
    return res;
  };

  const handleVerifyChain = async (): Promise<AuditVerifyResult> => {
    const res = await api.verifyAuditChain();
    setVerifyResult(res);
    if (!res.valid) {
      setRedAlert(res);
      addToast('error', 'Audit Verification FAILED', res.error || 'Cryptographic mismatch detected!');
    } else {
      setRedAlert(null);
      addToast('success', 'Audit Chain Verified (100%)', `Checked ${res.events_checked} events across 1 chain. Valid root.`);
    }
    return res;
  };

  const handleTamperBlock = (seq: number, mutatedPayload: Record<string, unknown>) => {
    engine.tamperBlock(seq, mutatedPayload);
    addToast('warning', 'Tamper Mutation Injected', `Altered block #${seq}. Click "Verify Chain" to inspect detection.`);
  };

  const handleRestoreLedger = async () => {
    await engine.restoreGenesisLedger();
    setRedAlert(null);
    setVerifyResult(null);
    addToast('info', 'Genesis Ledger Restored', 'Reset all audit blocks to cryptographic perfection.');
  };

  const handleResolveEscalation = async (id: string, decision: 'APPROVE' | 'REJECT', sticky = false) => {
    const esc = await api.resolveEscalation(id, decision, { sticky });
    if (esc) {
      addToast(
        decision === 'APPROVE' ? 'success' : 'warning',
        `Escalation ${decision}D`,
        `Escalation ${id} resolved via WebAuthn Biometric passkey.`
      );
    }
  };

  const handleUpdateOfferPrice = async (offerId: string, newAmount: string) => {
    const updated = await api.updateOfferPrice(offerId, newAmount);
    if (updated) {
      addToast('info', 'Price Updated', `Flight ${updated.flight_num || offerId} price set to $${newAmount}.`);
    }
  };

  const handleOpenDispute = (captureId: string, reason = 'UNAUTHORISED') => {
    const dsp = engine.openDispute(captureId, reason);
    addToast('warning', 'Dispute Opened', `Dispute ${dsp.dispute_id} opened for capture ${captureId}.`);
  };

  const handleSubmitDisputeEvidence = (disputeId: string) => {
    engine.submitDisputeEvidence(disputeId);
    addToast('success', 'Evidence Pack Submitted', `SD-JWT, detached JWS, and Merkle root submitted for dispute ${disputeId}.`);
  };

  const handleAdjudicateDispute = (disputeId: string, outcome: 'MERCHANT_WON' | 'BUYER_WON') => {
    engine.adjudicateDispute(disputeId, outcome);
    addToast(outcome === 'MERCHANT_WON' ? 'success' : 'warning', 'Dispute Adjudicated', `Outcome: ${outcome}`);
  };

  return (
    <AppContext.Provider
      value={{
        activeTab,
        setActiveTab,
        isLiveBackend,
        setIsLiveBackend,
        checkBackendStatus,
        mandates,
        activeMandate,
        setActiveMandate,
        createMandate: handleCreateMandate,
        revokeMandate: handleRevokeMandate,
        auditEvents,
        verifyResult,
        verifyChain: handleVerifyChain,
        tamperBlock: handleTamperBlock,
        restoreLedger: handleRestoreLedger,
        redAlert,
        setRedAlert,
        escalations,
        resolveEscalation: handleResolveEscalation,
        offers,
        updateOfferPrice: handleUpdateOfferPrice,
        receipts,
        vaultTokens,
        disputes,
        openDispute: handleOpenDispute,
        submitDisputeEvidence: handleSubmitDisputeEvidence,
        adjudicateDispute: handleAdjudicateDispute,
        telemetry,
        toasts,
        addToast,
        removeToast,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useApp = () => {
  const context = useContext(AppContext);
  if (!context) throw new Error('useApp must be used within an AppProvider');
  return context;
};
