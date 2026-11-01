// One Durable Object per channel (CONTRACT.md section 7).
// SQLite storage, WebSocket Hibernation API. Connecting never writes storage:
// the schema is created by the first notify.

import { DurableObject } from "cloudflare:workers";
import type { ChannelMessage } from "./validate";

export const BUFFER_MAX_AGE_MS = 15 * 60 * 1000;
export const BUFFER_MAX_ROWS = 500;
export const BUFFER_MAX_BYTES = 4 * 1024 * 1024;

export type NotifyStatus = "ok" | "duplicate" | "stale";
export interface NotifyResult {
  status: NotifyStatus;
  lastSeq: number;
}

interface Meta {
  last_seq: number;
  head: string | null;
  counts: string | null;
  continuous_since: number;
}

export class Channel extends DurableObject<Record<string, unknown>> {
  private schemaReady = false;

  constructor(ctx: DurableObjectState, env: Record<string, unknown>) {
    super(ctx, env);
    // Answered by the runtime without waking the object.
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  private get sql(): SqlStorage {
    return this.ctx.storage.sql;
  }

  /** Read-only: null when this object has never received a notify. */
  private readMeta(): Meta | null {
    if (!this.schemaReady) {
      const exists = this.sql
        .exec("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'")
        .toArray().length > 0;
      if (!exists) return null;
      this.schemaReady = true;
    }
    const rows = this.sql
      .exec<Record<string, SqlStorageValue>>("SELECT last_seq, head, counts, continuous_since FROM meta WHERE id = 1")
      .toArray();
    if (rows.length === 0) return null;
    const r = rows[0];
    return {
      last_seq: r.last_seq as number,
      head: (r.head as string | null) ?? null,
      counts: (r.counts as string | null) ?? null,
      continuous_since: r.continuous_since as number,
    };
  }

  private ensureSchema(): void {
    if (this.schemaReady) return;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS meta (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_seq INTEGER NOT NULL,
        head TEXT,
        counts TEXT,
        continuous_since INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS buffer (
        seq INTEGER PRIMARY KEY,
        body TEXT NOT NULL,
        at INTEGER NOT NULL,
        size INTEGER NOT NULL
      );
    `);
    this.schemaReady = true;
  }

  // GET /ws, forwarded by the Worker after validation.
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const channel = url.searchParams.get("channel") ?? "";
    const sinceRaw = url.searchParams.get("since");
    const since = sinceRaw === null ? null : Number(sinceRaw);

    const pair = new WebSocketPair();
    const [client, server] = [pair[0], pair[1]];
    this.ctx.acceptWebSocket(server);

    // Everything below is synchronous, so no notify can interleave between
    // the hello, the replayed frames and the first live frame.
    const meta = this.readMeta();
    const replay =
      meta !== null && since !== null && meta.continuous_since <= since && since <= meta.last_seq;
    const hello: Record<string, unknown> = {
      t: "hello",
      channel,
      seq: meta ? meta.last_seq : 0,
      head: meta ? meta.head : null,
      replay,
    };
    if (meta?.counts) hello.counts = JSON.parse(meta.counts);
    server.send(JSON.stringify(hello));
    if (replay) {
      const rows = this.sql.exec<{ body: string }>("SELECT body FROM buffer WHERE seq > ? ORDER BY seq", since);
      for (const row of rows) server.send(row.body);
    }
    return new Response(null, { status: 101, webSocket: client });
  }

  /**
   * Applies one channel's messages from one POST, in order.
   * A message whose seq is at or below the stored last seq is `duplicate` when the
   * same batch also carries the stored last seq (a retried POST whose earlier
   * attempt was applied); otherwise a lower seq is `stale`.
   */
  async notify(messages: ChannelMessage[]): Promise<NotifyResult[]> {
    this.ensureSchema();
    const startMeta = this.readMeta();
    const startLast = startMeta ? startMeta.last_seq : null;
    const retried = startLast !== null && messages.some((m) => m.seq === startLast);

    const results: NotifyResult[] = [];
    for (const m of messages) {
      const meta = this.readMeta();
      if (meta && m.seq <= meta.last_seq) {
        const dup = m.seq === meta.last_seq || (retried && startLast !== null && m.seq <= startLast);
        results.push({ status: dup ? "duplicate" : "stale", lastSeq: meta.last_seq });
        continue;
      }
      this.apply(meta, m);
      results.push({ status: "ok", lastSeq: m.seq });
      for (const ws of this.ctx.getWebSockets()) {
        try {
          ws.send(m.frame);
        } catch {
          // Socket already closing; the client reconnects with `since`.
        }
      }
    }
    return results;
  }

  private apply(meta: Meta | null, m: ChannelMessage): void {
    let continuousSince = meta ? meta.continuous_since : m.seq - 1;
    if (m.kind === "resync") {
      this.sql.exec("DELETE FROM buffer");
      continuousSince = m.seq;
    } else {
      const now = Date.now();
      this.sql.exec(
        "INSERT INTO buffer (seq, body, at, size) VALUES (?, ?, ?, ?)",
        m.seq,
        m.frame,
        now,
        m.size,
      );
      continuousSince = Math.max(continuousSince, this.prune(now));
    }
    this.sql.exec(
      `INSERT INTO meta (id, last_seq, head, counts, continuous_since) VALUES (1, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET last_seq = excluded.last_seq, head = excluded.head,
         counts = excluded.counts, continuous_since = excluded.continuous_since`,
      m.seq,
      m.head,
      m.counts,
      continuousSince,
    );
  }

  /** Drops rows older than 15 min, then the oldest beyond 500 rows or 4 MB. Returns the highest pruned seq (0 if none). */
  private prune(now: number): number {
    let pruned = 0;
    const old = this.sql
      .exec<{ s: number | null }>("SELECT MAX(seq) AS s FROM buffer WHERE at < ?", now - BUFFER_MAX_AGE_MS)
      .one().s;
    if (old !== null) {
      this.sql.exec("DELETE FROM buffer WHERE seq <= ?", old);
      pruned = old;
    }
    const { n, bytes } = this.sql
      .exec<{ n: number; bytes: number | null }>("SELECT COUNT(*) AS n, SUM(size) AS bytes FROM buffer")
      .one();
    if (n > BUFFER_MAX_ROWS || (bytes ?? 0) > BUFFER_MAX_BYTES) {
      // Walk from the newest row and keep what fits.
      let keptRows = 0;
      let keptBytes = 0;
      let cut: number | null = null;
      for (const row of this.sql.exec<{ seq: number; size: number }>("SELECT seq, size FROM buffer ORDER BY seq DESC")) {
        if (keptRows + 1 > BUFFER_MAX_ROWS || keptBytes + row.size > BUFFER_MAX_BYTES) {
          cut = row.seq;
          break;
        }
        keptRows++;
        keptBytes += row.size;
      }
      if (cut !== null) {
        this.sql.exec("DELETE FROM buffer WHERE seq <= ?", cut);
        pruned = Math.max(pruned, cut);
      }
    }
    return pruned;
  }

  async webSocketMessage(ws: WebSocket, _message: string | ArrayBuffer): Promise<void> {
    // `ping` never gets here (auto response). Anything else is a protocol violation.
    try {
      ws.close(1008, "unexpected frame");
    } catch {
      // already closed
    }
  }

  async webSocketClose(ws: WebSocket, code: number, _reason: string, _wasClean: boolean): Promise<void> {
    try {
      ws.close(code === 1005 || code === 1006 ? 1000 : code, "closing");
    } catch {
      // already closed
    }
  }

  async webSocketError(ws: WebSocket, _error: unknown): Promise<void> {
    try {
      ws.close(1011, "error");
    } catch {
      // already closed
    }
  }
}
