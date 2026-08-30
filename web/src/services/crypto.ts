// Cryptographic & Canonical Utilities for Aval (TryTrust)

/**
 * RFC 8785 JSON Canonicalization Scheme (JCS)
 * Recursively sort keys and produce deterministic string without extra spaces.
 */
export function canonicalJson(obj: unknown): string {
  if (obj === null || typeof obj !== 'object') {
    return JSON.stringify(obj);
  }

  if (Array.isArray(obj)) {
    return '[' + obj.map((item) => canonicalJson(item)).join(',') + ']';
  }

  const keys = Object.keys(obj as Record<string, unknown>).sort();
  const pairs = keys.map((key) => {
    const val = (obj as Record<string, unknown>)[key];
    return JSON.stringify(key) + ':' + canonicalJson(val);
  });

  return '{' + pairs.join(',') + '}';
}

/**
 * Convert buffer or Uint8Array to hex string
 */
export function buf2hex(buffer: ArrayBuffer | Uint8Array): string {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Convert string to Base64URL
 */
export function strToBase64Url(str: string): string {
  const bytes = new TextEncoder().encode(str);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

/**
 * Decode Base64URL to string
 */
export function base64UrlToStr(base64url: string): string {
  let base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
  while (base64.length % 4) {
    base64 += '=';
  }
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new TextDecoder().decode(bytes);
}

/**
 * SHA-256 asynchronous hash returning hex string
 */
export async function sha256Hex(data: string): Promise<string> {
  const msgBuffer = new TextEncoder().encode(data);
  if (typeof crypto !== 'undefined' && crypto.subtle) {
    const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
    return buf2hex(hashBuffer);
  }
  // Simple fallback hash for environments without WebCrypto
  let hash = 0;
  for (let i = 0; i < data.length; i++) {
    const char = data.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash |= 0;
  }
  return Math.abs(hash).toString(16).padStart(64, '0');
}

/**
 * SHA-256 returning Base64URL string (used for AP2 checkout_hash and SD digests)
 */
export async function sha256Base64Url(data: string): Promise<string> {
  const msgBuffer = new TextEncoder().encode(data);
  if (typeof crypto !== 'undefined' && crypto.subtle) {
    const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
    const bytes = new Uint8Array(hashBuffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  return strToBase64Url(data.slice(0, 32));
}

/**
 * Compute the deterministic hash for an audit ledger event:
 * H(seq + prev_hash + type + canonical_payload)
 */
export async function computeAuditEventHash(
  seq: number,
  prevHash: string,
  type: string,
  payload: Record<string, unknown>
): Promise<string> {
  const canonicalPayload = canonicalJson(payload);
  const content = `${seq}:${prevHash}:${type}:${canonicalPayload}`;
  return sha256Hex(content);
}

/**
 * Generate a simulated SD-JWT token structure with disclosures and Ed25519 signature
 */
export async function generateSimulatedSDJWT(claims: Record<string, unknown>): Promise<string> {
  const header = {
    alg: 'EdDSA',
    typ: 'sd-jwt',
    kid: 'issuer-key-v1',
  };

  const headerB64 = strToBase64Url(JSON.stringify(header));
  const payloadB64 = strToBase64Url(JSON.stringify(claims));
  
  // Simulated signature
  const sigHash = await sha256Hex(`${headerB64}.${payloadB64}.ed25519_root_secret`);
  const sigB64 = strToBase64Url(sigHash.slice(0, 64));

  // Disclosures for selective disclosure demo (e.g. email, address)
  const disclosure1 = strToBase64Url(JSON.stringify(['salt1_9a8b', 'email', 'marta@example.com']));
  const disclosure2 = strToBase64Url(JSON.stringify(['salt2_4c5d', 'shipping_address', 'Calle 100 #15-20, Bogota']));

  return `${headerB64}.${payloadB64}.${sigB64}~${disclosure1}~${disclosure2}~`;
}

/**
 * Generate a detached JWS signature for a canonical purchase intent
 */
export async function generateDetachedJWS(intent: Record<string, unknown>, agentId: string): Promise<string> {
  const header = {
    alg: 'EdDSA',
    b64: false,
    crit: ['b64'],
    kid: agentId,
  };
  const headerB64 = strToBase64Url(JSON.stringify(header));
  const canon = canonicalJson(intent);
  const sigDigest = await sha256Hex(`${headerB64}.${canon}.${agentId}_agent_private_key`);
  const sigB64 = strToBase64Url(sigDigest.slice(0, 64));
  return `${headerB64}..${sigB64}`;
}

/**
 * Generate simulated Checkout JWT signed by Merchant ES256
 */
export async function generateCheckoutJwt(offer: Record<string, unknown>, orderId: string): Promise<{ jwt: string; hash: string }> {
  const header = {
    alg: 'ES256',
    typ: 'checkout+jwt',
    kid: 'vuelaya-es256-key-v1',
  };
  const payload = {
    iss: 'https://merchant.vuelaya.example',
    aud: 'https://api.aval.example',
    order_id: orderId,
    offer,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 300,
  };

  const headerB64 = strToBase64Url(JSON.stringify(header));
  const payloadB64 = strToBase64Url(JSON.stringify(payload));
  const sigHash = await sha256Hex(`${headerB64}.${payloadB64}.merchant_es256_secret`);
  const sigB64 = strToBase64Url(sigHash.slice(0, 64));
  const jwt = `${headerB64}.${payloadB64}.${sigB64}`;
  const hash = await sha256Base64Url(jwt);

  return { jwt, hash };
}

/**
 * Shorten hash for UI badge display
 */
export function formatHash(hash: string, lead = 8, trail = 6): string {
  if (!hash) return '';
  if (hash.length <= lead + trail) return hash;
  return `${hash.slice(0, lead)}…${hash.slice(-trail)}`;
}
