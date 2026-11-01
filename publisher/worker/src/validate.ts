// Notify body validation (CONTRACT.md section 6). Runs only after the HMAC check.
// Self-contained (no imports) so the node unit tests can load it directly.

export const MAX_MESSAGES = 20;
export const MAX_MESSAGE_BYTES = 256 * 1024;

const NET_RE = /^net:[A-Za-z0-9-]{1,64}$/;
const DAO_RE = /^dao:[A-Za-z0-9-]{1,64}:0x[0-9a-f]{40}$/;

export function isValidChannel(channel: unknown): channel is string {
  return typeof channel === "string" && (NET_RE.test(channel) || DAO_RE.test(channel));
}

export type Kind = "changes" | "resync";

/** One validated message, ready for its Durable Object. */
export interface ChannelMessage {
  seq: number;
  kind: Kind;
  head: string | null;
  /** JSON text of `counts`, or null when absent. */
  counts: string | null;
  /** The server-to-client frame (section 7.3): `t` first, every message field copied unchanged. */
  frame: string;
  /** UTF-8 byte length of `frame`. */
  size: number;
}

export interface ParsedBody {
  /** Messages grouped by channel, each group in body order. */
  groups: Map<string, ChannelMessage[]>;
  /** Channel of each message, in body order (for the response). */
  order: string[];
}

export class BadRequest extends Error {
  status: 400 | 413;
  constructor(message: string, status: 400 | 413 = 400) {
    super(message);
    this.status = status;
  }
}

const encoder = new TextEncoder();

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function parseNotifyBody(text: string): ParsedBody {
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new BadRequest("body is not JSON");
  }
  if (!isPlainObject(body) || !Array.isArray(body.messages)) throw new BadRequest("messages must be an array");
  const messages = body.messages as unknown[];
  if (messages.length > MAX_MESSAGES) throw new BadRequest(`at most ${MAX_MESSAGES} messages`, 413);

  const groups = new Map<string, ChannelMessage[]>();
  const order: string[] = [];
  messages.forEach((raw, i) => {
    const where = `messages[${i}]`;
    if (!isPlainObject(raw)) throw new BadRequest(`${where} must be an object`);
    const { t: _ignored, ...msg } = raw;
    const { channel, seq, kind, head, counts, changes } = msg;
    if (!isValidChannel(channel)) throw new BadRequest(`${where}.channel invalid`);
    if (typeof seq !== "number" || !Number.isSafeInteger(seq) || seq < 1) {
      throw new BadRequest(`${where}.seq must be a positive safe integer`);
    }
    if (kind !== "changes" && kind !== "resync") throw new BadRequest(`${where}.kind invalid`);
    if (head !== null && (typeof head !== "string" || head.length === 0 || head.length > 128)) {
      throw new BadRequest(`${where}.head must be a string or null`);
    }
    if (counts !== undefined && counts !== null && !isPlainObject(counts)) {
      throw new BadRequest(`${where}.counts must be an object`);
    }
    if (kind === "changes" && !Array.isArray(changes)) throw new BadRequest(`${where}.changes must be an array`);
    if (kind === "resync" && changes !== undefined) throw new BadRequest(`${where}: resync carries no changes`);

    const rest = JSON.stringify(msg);
    const restBytes = encoder.encode(rest).byteLength;
    if (restBytes > MAX_MESSAGE_BYTES) {
      throw new BadRequest(`${where} exceeds ${MAX_MESSAGE_BYTES} bytes`, 413);
    }
    const frame = `{"t":"${kind}",${rest.slice(1)}`;

    let group = groups.get(channel);
    if (!group) groups.set(channel, (group = []));
    const prev = group[group.length - 1];
    if (prev && seq <= prev.seq) throw new BadRequest(`${where}.seq not increasing within its channel`);
    group.push({
      seq,
      kind,
      head: head as string | null,
      counts: isPlainObject(counts) ? JSON.stringify(counts) : null,
      frame,
      size: restBytes + frame.length - rest.length,
    });
    order.push(channel);
  });
  return { groups, order };
}

/** `since` query value: absent, or a non-negative safe integer. */
export function parseSince(raw: string | null): number | null | undefined {
  if (raw === null) return null;
  if (!/^\d{1,16}$/.test(raw)) return undefined;
  const n = Number(raw);
  return Number.isSafeInteger(n) ? n : undefined;
}
