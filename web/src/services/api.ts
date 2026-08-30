// Unified API Client for Aval (TryTrust)
// Connects to local FastAPI backend when running, or falls back transparently to the In-Memory Engine.

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
} from '../types';

class ApiClient {
  private useRealBackend = true;

  public setUseRealBackend(val: boolean) {
    this.useRealBackend = val;
  }

  public getUseRealBackend() {
    return this.useRealBackend;
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
        console.warn('Real agent /api/agent/ask call failed, falling back to local simulation', err);
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
        console.warn('Real backend fetch failed, falling back to simulation engine', err);
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
        console.warn('Real backend call failed, falling back to engine', err);
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
        console.warn('Real backend call failed, falling back to engine', err);
      }
    }
    return await engine.revokeMandate(mandateId);
  }

  // --- AUDIT & LEDGER ---
  public async getAuditEvents(): Promise<AuditEvent[]> {
    if (this.useRealBackend) {
      try {
        const res = await fetch('/api/audit/events');
        if (res.ok) return await res.json();
      } catch (err) {
        console.warn('Real backend call failed, falling back to engine', err);
      }
    }
    return engine.auditEvents;
  }

  public async verifyAuditChain(): Promise<AuditVerifyResult> {
    if (this.useRealBackend) {
      try {
        const res = await fetch('/api/audit/verify');
        if (res.ok) return await res.json();
      } catch (err) {
        console.warn('Real backend call failed, falling back to engine', err);
      }
    }
    return await engine.verifyAllChain();
  }

  // --- ESCALATIONS ---
  public async getEscalations(): Promise<Escalation[]> {
    if (this.useRealBackend) {
      try {
        const res = await fetch('/api/escalations');
        if (res.ok) return await res.json();
      } catch (err) {
        console.warn('Real backend call failed, falling back to engine', err);
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
        console.warn('Real backend call failed, falling back to engine', err);
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
        console.warn('Real merchant catalog call failed, falling back to engine', err);
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
        console.warn('Real merchant price update failed, falling back to engine', err);
      }
    }
    return engine.updateOfferPrice(offerId, newAmount);
  }

  // --- GATE & CHECKOUT ---
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
