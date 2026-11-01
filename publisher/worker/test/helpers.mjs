// Shared test helpers (node).
import { createHmac } from "node:crypto";

export function signNode(secret, timestamp, body) {
  return "v1=" + createHmac("sha256", secret).update(`${timestamp}.`).update(body).digest("hex");
}

export const DAO = (n) => `dao:Localhost:0x${n.toString(16).padStart(40, "0")}`;

/** A `changes` message whose serialized size is close to `bytes`. */
export function bigMessage(channel, seq, bytes) {
  const base = {
    channel,
    seq,
    kind: "changes",
    head: "0".repeat(32),
    counts: { proposals: 1, members: 1, activity: 1, projects: 0 },
    changes: [],
  };
  const doc = (i, pad) => ({
    path: `idaosLocalhost/0xAbC/proposals/${i}`,
    id: String(i),
    v: 1761998400123456 + i,
    data: { name: "x".repeat(pad), createdAt: { $ts: "2026-10-13T12:00:00.123456Z" }, inFavor: "1", n: i },
  });
  // Many ~2 KB docs, like a real flush, then pad the last one to the target size.
  let i = 0;
  while (JSON.stringify(base).length < bytes - 4000) base.changes.push(doc(i++, 1800));
  const fill = bytes - JSON.stringify(base).length - JSON.stringify(doc(i, 0)).length - 1;
  base.changes.push(doc(i, Math.max(0, fill)));
  return base;
}
