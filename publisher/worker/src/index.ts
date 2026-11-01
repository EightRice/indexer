// live.autonet.computer: push channel for the edge read layer (CONTRACT.md sections 6 and 7).

import { MAX_BODY_BYTES, verifyNotify } from "./auth";
import { BadRequest, isValidChannel, parseNotifyBody, parseSince } from "./validate";
import type { Channel, NotifyResult } from "./channel";

export { Channel } from "./channel";

export interface Env {
  CHANNEL: DurableObjectNamespace<Channel>;
  NOTIFY_SECRET?: string;
  NOTIFY_SECRET_PREV?: string;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function text(body: string, status: number): Response {
  return new Response(body, {
    status,
    headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
  });
}

async function handleWs(request: Request, url: URL, env: Env): Promise<Response> {
  if (request.method !== "GET") return text("method not allowed", 405);
  const channel = url.searchParams.get("channel");
  if (!isValidChannel(channel)) return text("bad channel", 400);
  const since = parseSince(url.searchParams.get("since"));
  if (since === undefined) return text("bad since", 400);
  if ((request.headers.get("upgrade") ?? "").toLowerCase() !== "websocket") {
    return text("expected websocket upgrade", 426);
  }
  // Rebuild the forwarded URL so the object only ever sees validated values.
  const fwd = new URL("https://channel/ws");
  fwd.searchParams.set("channel", channel);
  if (since !== null) fwd.searchParams.set("since", String(since));
  const stub = env.CHANNEL.get(env.CHANNEL.idFromName(channel));
  return stub.fetch(new Request(fwd.toString(), request));
}

/** Reads the body, stopping as soon as it exceeds `max` bytes (returns null then). */
async function readCapped(request: Request, max: number): Promise<Uint8Array | null> {
  if (!request.body) return new Uint8Array(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > max) {
      await reader.cancel().catch(() => {});
      return null;
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.byteLength;
  }
  return out;
}

async function handleNotify(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") return text("method not allowed", 405);
  if (!env.NOTIFY_SECRET && !env.NOTIFY_SECRET_PREV) return text("notify not configured", 503);

  // Length check before reading or authenticating anything else.
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_BODY_BYTES) return text("body too large", 413);
  // Content-Length can be absent (chunked) or wrong, so the read itself is capped too.
  const body = await readCapped(request, MAX_BODY_BYTES);
  if (body === null) return text("body too large", 413);

  const auth = await verifyNotify(
    body,
    request.headers.get("x-edge-timestamp"),
    request.headers.get("x-edge-signature"),
    [env.NOTIFY_SECRET, env.NOTIFY_SECRET_PREV],
    Math.floor(Date.now() / 1000),
  );
  if (!auth.ok) return text(auth.error, auth.status);

  let bodyText: string;
  try {
    bodyText = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(body);
  } catch {
    return text("body is not UTF-8", 400);
  }
  let parsed;
  try {
    parsed = parseNotifyBody(bodyText);
  } catch (e) {
    if (e instanceof BadRequest) return text(e.message, e.status);
    throw e;
  }

  // One RPC per channel keeps a channel's messages in order; channels run in parallel.
  const byChannel = new Map<string, NotifyResult[]>();
  await Promise.all(
    [...parsed.groups].map(async ([channel, messages]) => {
      const stub = env.CHANNEL.get(env.CHANNEL.idFromName(channel));
      byChannel.set(channel, await stub.notify(messages));
    }),
  );
  const cursor = new Map<string, number>();
  const results = parsed.order.map((channel) => {
    const i = cursor.get(channel) ?? 0;
    cursor.set(channel, i + 1);
    const r = byChannel.get(channel)![i];
    return { channel, status: r.status, lastSeq: r.lastSeq };
  });
  return json({ results });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    switch (url.pathname) {
      case "/health":
        return text("ok", 200);
      case "/ws":
        return handleWs(request, url, env);
      case "/notify":
        return handleNotify(request, env);
      default:
        return text("not found", 404);
    }
  },
} satisfies ExportedHandler<Env>;
