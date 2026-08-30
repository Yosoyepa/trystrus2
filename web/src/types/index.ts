// Aval (TryTrust) - Type Definitions matching api.yaml & schemas.md

export type ReasonCode =
  | 'AMOUNT_EXCEEDS_PER_TXN'
  | 'BUDGET_EXCEEDED'
  | 'LIMIT_EXHAUSTED'
  | 'CATEGORY_FORBIDDEN'
  | 'MERCHANT_NOT_ALLOWED'
  | 'MANDATE_EXPIRED'
  | 'MANDATE_NOT_YET_VALID'
  | 'MANDATE_REVOKED'
  | 'MANDATE_SUSPENDED'
  | 'MANDATE_EXHAUSTED'
  | 'CONDITION_FAILED'
  | 'INVALID_SIGNATURE'
  | 'INVALID_PROOF_OF_POSSESSION'
  | 'DUPLICATE_JTI'
  | 'NONCE_REUSED'
  | 'ESCALATION_TIMEOUT_DENIED'
  | 'RAIL_ERROR'
  | 'RAIL_TOKEN_DELETED';

export type Decision = 'APPROVED' | 'REJECTED' | 'ESCALATED';

export type MandateStatus = 'draft' | 'active' | 'suspended' | 'revoked' | 'expired' | 'exhausted';

export interface MandateLimits {
  max_per_txn: number;
  total_budget: number;
  max_txn: {
    count: number;
    period: 'day' | 'week' | 'month';
  };
}

export interface MandateScope {
  categories: string[];
  merchants: string[];
}

export interface MandateValidity {
  not_before: string;
  expires_at: string;
}

export interface MandateClaims {
  iss: string;
  iat: number;
  nbf: number;
  exp: number;
  jti: string;
  type: 'purchase_mandate_v1';
  sub: string;
  agent: string;
  cnf: {
    jwk: {
      kty: string;
      crv: string;
      x: string;
    };
  };
  payment_method_ref: string;
  currency: string;
  scope: MandateScope;
  conditions?: Record<string, unknown>;
  limits: MandateLimits;
  validity: MandateValidity;
  _sd_alg?: string;
  _sd?: string[];
  signed_with?: string;
}

export interface MandateView {
  mandate_id: string;
  jti: string;
  status: MandateStatus;
  limits: MandateLimits;
  spent: number;
  reserved: number;
  txn_count_period: number;
  payment_method_ref: string;
  created_at: string;
  sd_jwt?: string;
  claims?: MandateClaims;
}

export interface PurchaseIntent {
  typ: 'purchase_intent_v1';
  mandate_jti: string;
  agent: string;
  merchant_id: string;
  offer_id: string;
  amount: string; // Fixed 2-decimal string
  currency: string;
  nonce: string;
  jti: string;
  iat: number;
  exp: number;
  checkout_hash: string;
}

export interface VerifyRequest {
  intent_jwt: string;
  idempotency_key: string;
  agent_id?: string;
}

export interface VerifyResponse {
  decision: Decision;
  reason_code?: ReasonCode;
  reservation_id?: string;
  expires_in?: number;
  diff?: {
    limit: string;
    value: number;
    attempted: number;
    currency?: string;
  };
}

export interface PurchaseRequest {
  intent_jwt: string;
}

export type PurchaseStatusEnum =
  | 'pending_verification'
  | 'awaiting_escalation'
  | 'charging'
  | 'captured'
  | 'rejected'
  | 'compensated';

export interface Receipt {
  purchase_id: string;
  capture_id: string;
  amount: string;
  currency: string;
  captured_at: string;
  mandate_jti: string;
  simulated: boolean;
  offer_title?: string;
  merchant_id?: string;
  proof_signature?: string;
}

export interface PurchaseStatus {
  purchase_id: string;
  status: PurchaseStatusEnum;
  reason_code?: ReasonCode;
  receipt?: Receipt;
  escalation_id?: string;
  amount?: string;
  offer_id?: string;
}

export interface EscalationResolution {
  decision: 'APPROVE' | 'REJECT';
  approver: string;
  channel: 'telegram' | 'web';
  resolved_at: string;
  receipt_sig?: string;
}

export interface Escalation {
  id?: string;
  escalation_id: string;
  mandate_id: string;
  purchase_id: string;
  status: 'pending' | 'resolved' | 'expired';
  decision?: 'approved' | 'denied';
  approver?: string;
  channel?: string;
  receipt_sig?: string;
  resolved_at?: string;
  diff: {
    limit?: string;
    value?: number;
    attempted?: number;
    currency?: string;
    reason?: string;
    [k: string]: unknown;
  };
  timeout_at: string;
  created_at: string;
  resolution?: EscalationResolution;
  offer_title?: string;
  amount?: string;
  [k: string]: unknown;
}

export interface AuditEvent {
  seq: number;
  mandate_id: string;
  chain_key?: string;
  type: string;
  payload: Record<string, unknown>;
  prev_hash: string;
  hash: string;
  root_sig?: string;
  created_at: string;
  tampered?: boolean;
}

export interface AuditVerifyResult {
  valid: boolean;
  events_checked: number;
  chains_checked?: number;
  last_root: string;
  last_root_verified: boolean;
  error?: string;
  broken_seq?: number;
  expected_hash?: string;
  actual_hash?: string;
}

// Mirrors src/api/evidence/models.py EvidencePack.to_dict() exactly — the real shape
// served by GET /purchases/{purchase_id}/evidence-pack. Every field is either real
// backend data or an honest null; nothing here is invented on the frontend.
export interface EvidenceChainVerdict {
  ok: boolean;
  first_bad_seq: number | null;
  reason: string | null;
}

export interface EvidencePack {
  purchase_id: string;
  mandate_jti: string;
  integrity: 'ok' | 'failed';
  generated_at: string;
  digest: string;
  mandate_claims: Record<string, unknown> | null;
  intent: Record<string, unknown> | null;
  decision: Record<string, unknown> | null;
  receipt: Record<string, unknown> | null;
  ledger_events: Record<string, unknown>[];
  chain: EvidenceChainVerdict | null;
  root_checkpoint: Record<string, unknown> | null;
  failure_reasons: string[];
}

export interface Offer {
  offer_id: string;
  merchant_id: string;
  category: string;
  title: string;
  amount: string;
  currency: string;
  origin: string;
  destination: string;
  date: string;
  description: string;
  carrier?: string;
  flight_num?: string;
  departure_time?: string;
  arrival_time?: string;
}

export interface CheckoutQuote {
  order_id: string;
  offer: Offer;
  checkout_jwt: string;
  checkout_hash: string;
}

export interface VaultToken {
  token_id: string;
  mandate_jti: string;
  card_brand: string;
  last4: string;
  status: 'ACTIVE' | 'DELETED';
  created_at: string;
  deleted_at?: string;
}

export interface DisputeRecord {
  dispute_id: string;
  capture_id: string;
  purchase_id: string;
  amount: string;
  currency: string;
  reason: string;
  status: 'OPEN' | 'UNDER_REVIEW' | 'MERCHANT_WON' | 'BUYER_WON';
  evidence_submitted: boolean;
  created_at: string;
  evidence_bundle?: {
    mandate_jti: string;
    sd_jwt_hash: string;
    intent_detached_sig: string;
    checkout_hash: string;
    audit_chain_seq: number;
  };
}

export interface TelemetryState {
  active_advisory_locks: Array<{ lock_name: string; acquired_at: string; pid: number }>;
  rate_limit: {
    capacity: number;
    tokens_remaining: number;
    fill_rate: string;
  };
  spend_counters: {
    total_spent: number;
    total_reserved: number;
    budget_limit: number;
  };
  outbox_queue: {
    depth: number;
    processed_count: number;
    last_relayed_at: string;
  };
  relay_status: 'HEALTHY' | 'PROCESSING' | 'DEGRADED';
  avg_latency_ms: number;
}

export type AgentNode =
  | 'idle'
  | 'perceive'
  | 'search'
  | 'propose'
  | 'gate'
  | 'receipt'
  | 'await_human'
  | 'rejected';

export interface AgentChatMessage {
  id: string;
  sender: 'user' | 'agent' | 'system';
  text: string;
  timestamp: string;
  node?: AgentNode;
  metadata?: Record<string, unknown>;
}

export interface ScenarioStep {
  id: string;
  title: string;
  description: string;
  status: 'pending' | 'running' | 'passed' | 'failed';
  details?: string;
  log?: string[];
}

export interface DemoScenario {
  id: number;
  title: string;
  tagline: string;
  description: string;
  category: 'Core' | 'Security' | 'Adversarial' | 'Audit' | 'Automation';
  status: 'idle' | 'running' | 'passed' | 'failed';
  steps: ScenarioStep[];
  evidence?: Record<string, unknown>;
}

export interface ToastMessage {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  title: string;
  message: string;
  timestamp: number;
}

export type PipelineStepStatus = 'pending' | 'running' | 'passed' | 'failed';

export interface PipelineStep {
  step: number;
  name: string;
  status: PipelineStepStatus;
  details: string;
}
