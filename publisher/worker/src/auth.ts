// Notify authentication (CONTRACT.md section 6).
// Self-contained (no imports) so the node unit tests can load it directly.

export const MAX_BODY_BYTES = 512 * 1024;
export const MAX_SKEW_SECONDS = 300;

export type AuthResult =
  | { ok: true }
  | { ok: false; status: 401 | 413; error: string };

const encoder = new TextEncoder();

function hexToBytes(hex: string): Uint8Array | null {
  if (hex.length !== 64 || !/^[0-9a-f]+$/.test(hex)) return null;
  const out = new Uint8Array(32);
  for (let i = 0; i < 32; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function signedBytes(timestamp: string, body: Uint8Array): Uint8Array {
  const prefix = encoder.encode(timestamp + ".");
  const data = new Uint8Array(prefix.length + body.length);
  data.set(prefix, 0);
  data.set(body, prefix.length);
  return data;
}

async function hmacKey(secret: string, usage: "sign" | "verify"): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    [usage],
  );
}

/** `v1=` + lowercase hex HMAC-SHA256(secret, timestamp + "." + body). */
export async function sign(secret: string, timestamp: string, body: Uint8Array | string): Promise<string> {
  const bytes = typeof body === "string" ? encoder.encode(body) : body;
  const key = await hmacKey(secret, "sign");
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, signedBytes(timestamp, bytes)));
  let hex = "";
  for (const b of mac) hex += b.toString(16).padStart(2, "0");
  return "v1=" + hex;
}

/**
 * Checks, in order: body length, timestamp window, HMAC over the raw bytes
 * (crypto.subtle.verify compares in constant time). The body is never parsed here.
 */
export async function verifyNotify(
  body: Uint8Array,
  timestampHeader: string | null,
  signatureHeader: string | null,
  secrets: Array<string | undefined>,
  nowSeconds: number,
): Promise<AuthResult> {
  if (body.byteLength > MAX_BODY_BYTES) return { ok: false, status: 413, error: "body too large" };
  if (!timestampHeader || !/^\d{1,15}$/.test(timestampHeader)) {
    return { ok: false, status: 401, error: "bad timestamp" };
  }
  if (Math.abs(nowSeconds - Number(timestampHeader)) > MAX_SKEW_SECONDS) {
    return { ok: false, status: 401, error: "timestamp outside window" };
  }
  if (!signatureHeader || !signatureHeader.startsWith("v1=")) {
    return { ok: false, status: 401, error: "bad signature" };
  }
  const mac = hexToBytes(signatureHeader.slice(3));
  if (!mac) return { ok: false, status: 401, error: "bad signature" };
  const data = signedBytes(timestampHeader, body);
  for (const secret of secrets) {
    if (!secret) continue;
    const key = await hmacKey(secret, "verify");
    if (await crypto.subtle.verify("HMAC", key, mac, data)) return { ok: true };
  }
  return { ok: false, status: 401, error: "bad signature" };
}
