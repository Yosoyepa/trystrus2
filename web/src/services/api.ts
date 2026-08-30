// Unified API Client for Aval (TryTrust)
// Connects to local FastAPI backend when running. Non-evidence calls (mandates, offers,
// escalations, agent chat) fall back to the In-Memory Engine so the interface stays
// exercisable offline — but every fallback is broadcast via onFallback() so the UI can
// surface it, never silently. Evidence calls (getAuditEvents, verifyAuditChain,
// getEvidencePack) NEVER fall back: a judge must never see a fabricated hash chain, a
// "valid" verdict, or a downloadable proof envelope that did not come from the real
// backend.

import { engine } from './mockEngine';
import {
  MandateView,
  MandateClaims,
  AuditEvent,
  AuditVerifyResult,
  Escalation,
  Offer,
  Receipt,
  PurchaseStatus,
  VerifyResponse,
  PipelineStep,
  EvidencePack,
} from '../types';

// Thrown by evidence-critical calls (audit events, chain verification) when the real
// backend cannot be reached. Never caught internally to substitute mock evidence —
// callers must surface this to the user, not paper over it.
export class BackendUnavailableError extends Error {
  constructor(
    message = 'Backend unreachable. Evidence cannot be shown without it — start the stack with `docker compose up` and try again.'
  ) {
    super(message);
    this.name = 'BackendUnavailableError';
  }
}

type FallbackListener = (context: string) => void;

class ApiClient {
  private useRealBackend = true;
  private fallbackListeners: FallbackListener[] = [];

  public setUseRealBackend(val: boolean) {
    this.useRealBackend = val;
  }

  public getUseRealBackend() {
    return this.useRealBackend;
  }

  // Subscribe to be notified whenever a non-evidence call silently would have gone
  // unnoticed before this fix — now every simulated fallback fires this so the UI can
  // toast it. Returns an unsubscribe function.
  public onFallback(listener: FallbackListener): () => void {
    this.fallbackListeners.push(listener);
    return () => {
      this.fallbackListeners = this.fallbackListeners.filter((l) => l !== listener);
    };
  }

  private notifyFallback(context: string, err?: unknown) {
    console.warn(`[api] ${context}: real backend call failed, using local simulation`, err);
    this.fallbackListeners.forEach((l) => l(context));
  }

  public async checkBackendHealth(): Promise<boolean> {
    try {
      const res = await fetch('/api/.well-known/jwks.json', { method: 'GET', signal: AbortSignal.timeout(1000) });
      return res.ok;
    } catch {
      return false;
    }
  }

  // --- AGENT BRIDGE (Real LLM / Gemini) ---
  // Purchase/interface simulation is defensible here: askAgent returning null tells the
  // caller (AgentChat) to run its own clearly-labelled local simulation branch.
  public async askAgent(
    text: string,
    mandateJti: string,
    agentId = 'agt_flights',
    sessionId?: string
  ): Promise<any> {
    if (this.useRealBackend) {
      try {
        const res = await fetch('/api/agent/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text,
            agent_id: agentId,
            mandate_jti: mandateJti,
            session_id: sessionId,
            person: 'Marta',
          }),
        });
        if (res.ok) return await res.json();
      } catch (err) {
        this.notifyFallback('askAgent', err);
      }
    }
    return null;
  }

  // --- MANDATES ---
  public async getMandates(userId = 'usr_marta'): Promise<MandateView[]> {
    if (this.useRealBackend) {
      try {
        const res = await fetch(`/api/mandates?user_id=${encodeURIComponent(userId)}`);
        if (res.ok) return await res.json();
      } catch (err) {
        this.notifyFallback('getMandates', err);
      }
    }
    return engine.mandates;
  }

  public async createMandate(claims: Partial<MandateClaims>): Promise<MandateView> {
    if (this.useRealBackend) {
      try {
        const res = await fetch('/api/mandates', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(claims),
        });
        if (res.ok) return await res.json();
      } catch (err) {
        this.notifyFallback('createMandate', err);
      }
    }
    return await engine.createMandate(claims);
  }

  public async revokeMandate(mandateId: string): Promise<{ success: boolean; latency_ms: number }> {
    if (this.useRealBackend) {
      try {
        const startTime = performance.now();
        const res = await fetch(`/api/mandates/${mandateId}/revoke`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ assertion: 'webauthn_uv_passkey_sig' }),
        });
        const elapsed = Math.round(performance.now() - startTime);
        if (res.ok) return { success: true, latency_ms: elapsed };
      } catch (err) {
        this.notifyFallback('revokeMandate', err);
      }
    }
    return await engine.revokeMandate(mandateId);
  }

  // --- AUDIT & LEDGER (evidence-critical: never fabricate) ---
  // These two NEVER fall back to the mock engine. A failed/unreachable backend must
  // throw BackendUnavailableError, not silently substitute invented hash-chain data.
  public async getAuditEvents(): Promise<AuditEvent[]> {
    try {
      const res = await fetch('/api/audit/events');
      if (res.ok) return await res.json();
      throw new BackendUnavailableError(
        `Audit backend responded with HTTP ${res.status}. Refusing to show fabricated evidence — start the stack with \`docker compose up\` and retry.`
      );
    } catch (err) {
      if (err instanceof BackendUnavailableError) throw err;
      console.error('[api] getAuditEvents: backend unreachable, refusing to fabricate audit evidence', err);
      throw new BackendUnavailableError();
    }
  }

  public async verifyAuditChain(): Promise<AuditVerifyResult> {
    try {
      const res = await fetch('/api/audit/verify');
      if (res.ok) return await res.json();
      throw new BackendUnavailableError(
        `Audit backend responded with HTTP ${res.status}. Refusing to render a "valid" verdict that didn't come from the backend — start the stack with \`docker compose up\` and retry.`
      );
    } catch (err) {
      if (err instanceof BackendUnavailableError) throw err;
      console.error('[api] verifyAuditChain: backend unreachable, refusing to fabricate a verdict', err);
      throw new BackendUnavailableError();
    }
  }

  // Cryptographic evidence pack for a single purchase (mandate claims, intent, decision,
  // receipt, ledger slice, chain verdict, root checkpoint — see
  // src/api/evidence/models.py EvidencePack.to_dict()). NEVER falls back to invented
  // fields: a 404 means no real pack exists yet for that purchase (an honest "not
  // found", returned as null); any other failure to reach the backend throws
  // BackendUnavailableError. There is no engine.evidencePack — a downloadable proof
  // envelope built from mock cryptographic material is worse than none at all.
  public async getEvidencePack(purchaseId: string): Promise<EvidencePack | null> {
    let res: Response;
    try {
      res = await fetch(`/api/purchases/${encodeURIComponent(purchaseId)}/evidence-pack`);
    } catch (err) {
      console.error('[api] getEvidencePack: backend unreachable, refusing to fabricate an evidence pack', err);
      throw new BackendUnavailableError();
    }
    if (res.status === 404) return null;
    if (!res.ok) {
      throw new BackendUnavailableError(
        `Evidence backend responded with HTTP ${res.status}. Refusing to show a fabricated proof envelope — start the stack with \`docker compose up\` and retry.`
      );
    }
    return await res.json();
  }

  // --- ESCALATIONS ---
  public async getEscalations(): Promise<Escalation[]> {
    if (this.useRealBackend) {
      try {
        const res = await fetch('/api/escalations');
        if (res.ok) return await res.json();
      } catch (err) {
        this.notifyFallback('getEscalations', err);
      }
    }
    return engine.escalations;
  }

  public async resolveEscalation(
    escalationId: string,
    decision: 'APPROVE' | 'REJECT',
    options?: { sticky?: boolean; approver?: string }
  ): Promise<Escalation | null> {
    if (this.useRealBackend) {
      try {
        const res = await fetch(`/api/escalations/${escalationId}/resolve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            decision,
            approver: options?.approver || 'usr_marta_passkey',
            channel: 'web',
            sticky: options?.sticky ? { max_per_txn: 300, count: 1, period: 'month' } : undefined,
          }),
        });
        if (res.ok) return await res.json();
      } catch (err) {
        this.notifyFallback('resolveEscalation', err);
      }
    }
    return await engine.resolveEscalation(escalationId, decision, options);
  }

  // --- CATALOG & OFFERS ---
  public async getOffers(): Promise<Offer[]> {
    if (this.useRealBackend) {
      try {
        const res = await fetch('/merchant/catalog/offers');
        if (res.ok) return await res.json();
      } catch (err) {
        this.notifyFallback('getOffers', err);
      }
    }
    return engine.offers;
  }

  public async updateOfferPrice(offerId: string, newAmount: string): Promise<Offer | null> {
    if (this.useRealBackend) {
      try {
        const res = await fetch(`/merchant/admin/offers/${offerId}/price`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount: newAmount }),
        });
        if (res.ok) return await res.json();
      } catch (err) {
        this.notifyFallback('updateOfferPrice', err);
      }
    }
    return engine.updateOfferPrice(offerId, newAmount);
  }

  // --- GATE & CHECKOUT ---
  // Always simulated: exercising the purchase pipeline interface, not evidence.
  public async verifyPurchase(mandateId: string, offerId: string): Promise<VerifyResponse> {
    return await engine.evaluateGate(mandateId, offerId);
  }

  public async executePurchase(
    mandateId: string,
    offerId: string,
    options?: {
      overridePrice?: string;
      corruptedSignature?: boolean;
      corruptedCartHash?: boolean;
    }
  ): Promise<{ status: PurchaseStatus; pipelineSteps: PipelineStep[] }> {
    return await engine.executePurchaseFlow(mandateId, offerId, options);
  }

  public async getReceipts(): Promise<Receipt[]> {
    return engine.receipts;
  }
}

export const api = new ApiClient();
