// Integration tests against `wrangler dev` on the e2e port (CONTRACT.md section 11: 8797, inspector 9239).
// Starts its own wrangler dev with a private persist dir, runs every Worker acceptance check
// from section 12, then stops only the process tree it started.
//
//   node test/e2e.mjs            start wrangler dev, test, stop it
//   E2E_EXTERNAL=1 node test/e2e.mjs   test an already running instance on 8797 (secrets must match below)

import { spawn, execFileSync } from "node:child_process";
import { existsSync, readdirSync, rmSync, statSync } from "node:fs";
import { createServer } from "node:net";
import { request as httpRequest } from "node:http";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";
import { bigMessage, DAO, signNode } from "./helpers.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 8797;
const INSPECTOR = 9239;
const BASE = `http://127.0.0.1:${PORT}`;
const WS_BASE = `ws://127.0.0.1:${PORT}`;
const PERSIST = join(ROOT, ".wrangler", "worker-test");
const SECRET = "dev-secret";
const SECRET_PREV = "dev-secret-prev";
const EXTERNAL = process.env.E2E_EXTERNAL === "1";

let child = null;
let output = "";
let failures = 0;
let passes = 0;

function ok(cond, msg) {
  if (cond) {
    passes++;
    console.log(`  ok   ${msg}`);
  } else {
    failures++;
    console.log(`  FAIL ${msg}`);
  }
}
function eq(a, b, msg) {
  const sa = JSON.stringify(a), sb = JSON.stringify(b);
  ok(sa === sb, sa === sb ? msg : `${msg}\n         got      ${sa.slice(0, 400)}\n         expected ${sb.slice(0, 400)}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rnd = () => Math.floor(Math.random() * 2 ** 40) + 1;

function portFree(port) {
  return new Promise((resolve) => {
    const s = createServer().once("error", () => resolve(false)).once("listening", () => s.close(() => resolve(true)));
    s.listen(port, "127.0.0.1");
  });
}

async function startWrangler() {
  for (const p of [PORT, INSPECTOR]) {
    if (!(await portFree(p))) throw new Error(`port ${p} is busy; refusing to start (set E2E_EXTERNAL=1 to reuse)`);
  }
  rmSync(PERSIST, { recursive: true, force: true });
  const args = [
    join(ROOT, "node_modules", "wrangler", "bin", "wrangler.js"),
    "dev", "--ip", "127.0.0.1", "--port", String(PORT), "--inspector-port", String(INSPECTOR),
    "--persist-to", PERSIST, "--var", `NOTIFY_SECRET:${SECRET}`, "--var", `NOTIFY_SECRET_PREV:${SECRET_PREV}`,
    "--show-interactive-dev-session=false", "--log-level", "info",
  ];
  child = spawn(process.execPath, args, { cwd: ROOT, env: { ...process.env, WRANGLER_SEND_METRICS: "false", CI: "1" } });
  child.stdout.on("data", (d) => (output += d));
  child.stderr.on("data", (d) => (output += d));
  console.log(`started wrangler dev, pid ${child.pid}`);
  for (let i = 0; i < 120; i++) {
    try {
      const r = await fetch(`${BASE}/health`);
      if (r.ok) return;
    } catch {}
    if (child.exitCode !== null) break;
    await sleep(500);
  }
  throw new Error("wrangler dev did not come up:\n" + output);
}

function stopWrangler() {
  if (!child || child.exitCode !== null) return;
  try {
    if (process.platform === "win32") execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    else child.kill("SIGTERM");
  } catch {}
  console.log(`stopped wrangler dev, pid ${child.pid}`);
}

/** GET with an Upgrade header (fetch refuses to send one). Resolves the status code. */
function upgradeStatus(path) {
  return new Promise((resolve, reject) => {
    const req = httpRequest(`${BASE}${path}`, {
      headers: { connection: "Upgrade", upgrade: "websocket", "sec-websocket-version": "13", "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==" },
    });
    req.on("response", (res) => { res.resume(); resolve(res.statusCode); });
    req.on("upgrade", (res, socket) => { socket.destroy(); resolve(res.statusCode); });
    req.on("error", reject);
    req.end();
  });
}

async function postRaw(body, { secret = SECRET, ts = Math.floor(Date.now() / 1000), sig } = {}) {
  const headers = { "content-type": "application/json", "x-edge-timestamp": String(ts) };
  headers["x-edge-signature"] = sig ?? signNode(secret, String(ts), body);
  const r = await fetch(`${BASE}/notify`, { method: "POST", headers, body });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch {}
  return { status: r.status, text, json };
}
const notify = (messages, opts) => postRaw(JSON.stringify({ messages }), opts);

const msg = (channel, seq, extra = {}) => ({
  channel, seq, kind: "changes", head: `h${seq}`, counts: { proposals: seq % 7, members: 1, activity: 2, projects: 0 },
  changes: [{ path: `idaosLocalhost/0xAbC/proposals/${seq}`, id: String(seq), v: 1761998400000000 + seq, data: { n: seq } }],
  ...extra,
});
const resync = (channel, seq) => ({ channel, seq, kind: "resync", head: `r${seq}`, counts: { proposals: 0, members: 0, activity: 0, projects: 0 } });

/** Opens a socket and collects frames. */
function connect(channel, since) {
  const url = `${WS_BASE}/ws?channel=${encodeURIComponent(channel)}` + (since !== undefined ? `&since=${since}` : "");
  const ws = new WebSocket(url);
  const frames = [];
  const waiters = [];
  let closed = null;
  ws.addEventListener("message", (e) => {
    frames.push(e.data);
    waiters.splice(0).forEach((w) => w());
  });
  ws.addEventListener("close", (e) => {
    closed = { code: e.code };
    waiters.splice(0).forEach((w) => w());
  });
  const opened = new Promise((res, rej) => {
    ws.addEventListener("open", res, { once: true });
    ws.addEventListener("error", rej, { once: true });
  });
  return {
    ws, frames, opened,
    get closed() { return closed; },
    json: () => frames.map((f) => JSON.parse(f)),
    async waitFor(n, ms = 5000) {
      const end = Date.now() + ms;
      while (frames.length < n && !closed && Date.now() < end) {
        await Promise.race([new Promise((r) => waiters.push(r)), sleep(end - Date.now())]);
      }
      return frames.length >= n;
    },
    async waitClose(ms = 5000) {
      const end = Date.now() + ms;
      while (!closed && Date.now() < end) await Promise.race([new Promise((r) => waiters.push(r)), sleep(50)]);
      return closed;
    },
    close() { try { ws.close(1000); } catch {} },
  };
}

function snapshot(dir) {
  const out = {};
  const walk = (d) => {
    if (!existsSync(d)) return;
    for (const name of readdirSync(d)) {
      const p = join(d, name);
      const st = statSync(p);
      if (st.isDirectory()) walk(p);
      else out[p] = `${st.size}:${st.mtimeMs}`;
    }
  };
  walk(dir);
  return out;
}

function userTables(file) {
  try {
    const db = new DatabaseSync(file, { readOnly: true });
    const rows = db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all();
    db.close();
    // Local miniflare keeps its own `__miniflare_do_name` table per object (simulator
    // bookkeeping for idFromName, not present in production); every other table is ours.
    return rows.map((r) => r.name).filter((n) => !n.startsWith("__miniflare") && !n.startsWith("_cf_"));
  } catch (e) {
    return [`unreadable: ${e.message}`];
  }
}

async function main() {
  if (!EXTERNAL) await startWrangler();

  console.log("endpoints");
  eq(await (await fetch(`${BASE}/health`)).text(), "ok", "GET /health is ok");
  eq((await fetch(`${BASE}/ws?channel=net:Localhost`)).status, 426, "/ws without Upgrade is 426");
  eq(await upgradeStatus("/ws?channel=dao:Localhost:0xABC"), 400, "/ws bad channel is 400");
  eq(await upgradeStatus("/ws?channel=net:Localhost&since=-1"), 400, "/ws bad since is 400");
  eq((await fetch(`${BASE}/nope`)).status, 404, "unknown path is 404");
  eq((await fetch(`${BASE}/notify`)).status, 405, "GET /notify is 405");

  console.log("notify authentication and limits");
  const A = DAO(rnd());
  const empty = '{"messages":[]}';
  eq((await fetch(`${BASE}/notify`, { method: "POST", body: empty })).status, 401, "no signature headers: 401");
  eq((await postRaw(empty, { secret: "wrong" })).status, 401, "wrong secret: 401");
  const now = Math.floor(Date.now() / 1000);
  eq((await postRaw(empty, { ts: now - 301 })).status, 401, "timestamp 301 s old, correctly signed: 401");
  eq((await postRaw(empty, { ts: now + 301 })).status, 401, "timestamp 301 s ahead, correctly signed: 401");
  eq((await postRaw(empty, { ts: now - 250 })).status, 200, "timestamp 250 s old: 200");
  const vec = await postRaw(empty, { ts: 1730000000, sig: "v1=8cc09b05dd38fe0709b20698d82926fdf629b028c26ed77bd47929dff3df2a15" });
  eq(vec.status, 401, "spec test vector signature with its 2024 timestamp is outside the window: 401");
  const prev = await notify([], { secret: SECRET_PREV });
  eq([prev.status, prev.json], [200, { results: [] }], "NOTIFY_SECRET_PREV accepted (rotation)");
  const tampered = '{"messages":[ ]}';
  eq((await postRaw(tampered, { sig: signNode(SECRET, String(now), empty), ts: now })).status, 401, "body changed after signing: 401");
  const huge = JSON.stringify({ messages: [bigMessage(A, 1, 300 * 1024), bigMessage(A, 2, 300 * 1024)] });
  eq((await postRaw(huge)).status, 413, `body of ${huge.length} bytes: 413`);
  const chunked = await fetch(`${BASE}/notify`, {
    method: "POST",
    duplex: "half",
    headers: { "content-type": "application/json", "x-edge-timestamp": String(now), "x-edge-signature": signNode(SECRET, String(now), huge) },
    body: new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(huge)); c.close(); } }),
  });
  eq(chunked.status, 413, "oversize chunked body without Content-Length: 413");
  const badUtf8 = Buffer.from([0x7b, 0x22, 0x6d, 0x22, 0x3a, 0x22, 0xff, 0x22, 0x7d]);
  eq((await postRaw(badUtf8)).status, 400, "signed body that is not UTF-8: 400");
  eq((await notify(Array.from({ length: 21 }, (_, i) => msg(A, 10 + i)))).status, 413, "21 messages: 413");
  eq((await notify([bigMessage(A, 1, 257 * 1024)])).status, 413, "single message over 256 KB: 413");
  eq((await postRaw("{not json")).status, 400, "signed non-JSON body: 400");
  eq((await notify([{ ...msg(A, 1), channel: "dao:Localhost:0xNOPE" }])).status, 400, "invalid channel: 400");
  eq((await notify([msg(A, 5), msg(A, 5)])).status, 400, "non-increasing seq within a channel: 400");

  console.log("seq handling, fan-out, pass-through");
  const w1 = connect(A);
  await w1.opened;
  await w1.waitFor(1);
  eq(w1.json()[0], { t: "hello", channel: A, seq: 0, head: null, replay: false }, "hello on a fresh channel: seq 0, head null, no counts");
  const first = msg(A, 1000, { future: { x: [1, "two"] } });
  const r1 = await notify([first]);
  eq(r1.json, { results: [{ channel: A, status: "ok", lastSeq: 1000 }] }, "first message ok");
  await w1.waitFor(2);
  eq(w1.json()[1], { t: "changes", ...first }, "live frame copies every field, unknown ones included");
  eq((await notify([first])).json, { results: [{ channel: A, status: "duplicate", lastSeq: 1000 }] }, "same seq again: duplicate");
  eq((await notify([msg(A, 900)])).json, { results: [{ channel: A, status: "stale", lastSeq: 1000 }] }, "lower seq: stale");
  const pair = [msg(A, 1001), msg(A, 1002)];
  eq((await notify(pair)).json.results.map((r) => r.status), ["ok", "ok"], "two messages one POST: ok ok");
  eq((await notify(pair)).json, { results: [{ channel: A, status: "duplicate", lastSeq: 1002 }, { channel: A, status: "duplicate", lastSeq: 1002 }] },
    "the same POST retried after it was applied: duplicate duplicate (not stale)");
  const partial = await notify([msg(A, 1002), msg(A, 1003)]);
  eq(partial.json.results.map((r) => [r.status, r.lastSeq]), [["duplicate", 1002], ["ok", 1003]],
    "retried POST applied only up to its first message: duplicate, then ok");
  const N = DAO(rnd());
  const mixed = await notify([msg(A, 1004), resync(N, 50), msg(A, 1005)]);
  eq(mixed.json.results.map((r) => [r.channel, r.status, r.lastSeq]), [[A, "ok", 1004], [N, "ok", 50], [A, "ok", 1005]],
    "results follow body order across channels");
  await w1.waitFor(6);
  await sleep(200);
  eq(w1.json().slice(1).map((f) => f.seq), [1000, 1001, 1002, 1003, 1004, 1005], "live socket saw each accepted message once, in order");

  console.log("replay");
  const w2 = connect(A, 1002);
  await w2.waitFor(4);
  await sleep(300);
  const f2 = w2.json();
  eq(f2[0], { t: "hello", channel: A, seq: 1005, head: "h1005", replay: true, counts: msg(A, 1005).counts }, "since inside buffer: hello replay:true with head and counts");
  eq(f2.slice(1).map((f) => f.seq), [1003, 1004, 1005], "replays exactly the missed messages (1003..1005), nothing else");
  eq(f2[1], { t: "changes", ...msg(A, 1003) }, "replayed frame identical to the live frame");
  await notify([msg(A, 1006)]);
  await w2.waitFor(5);
  eq(w2.json()[4]?.seq, 1006, "then live");
  const w3 = connect(A, 999);
  await w3.waitFor(8);
  await sleep(200);
  eq(w3.json().map((f) => f.replay ?? f.seq), [true, 1000, 1001, 1002, 1003, 1004, 1005, 1006], "since = continuous_since (first seq - 1) replays everything");
  const w4 = connect(A, 998);
  await w4.waitFor(1);
  await sleep(200);
  eq([w4.json().length, w4.json()[0].replay, w4.json()[0].seq, w4.json()[0].head], [1, false, 1006, "h1006"], "since older than continuous_since: replay:false, current head, no frames");
  const w5 = connect(A, 5000);
  await w5.waitFor(1);
  eq(w5.json()[0].replay, false, "since ahead of last_seq: replay:false");
  const w6 = connect(A, 1006);
  await w6.waitFor(1);
  await sleep(200);
  eq([w6.json().length, w6.json()[0].replay], [1, true], "since = last_seq: replay:true, nothing to send");

  const rs = resync(A, 1100);
  eq((await notify([rs])).json.results[0].status, "ok", "resync accepted");
  await w1.waitFor(9);
  eq(w1.json()[8], { t: "resync", ...rs }, "resync frame broadcast");
  const w7 = connect(A, 1050);
  await w7.waitFor(1);
  await sleep(200);
  eq([w7.json().length, w7.json()[0].replay, w7.json()[0].head], [1, false, "r1100"], "since before the resync: replay:false");
  const w8 = connect(A, 1100);
  await w8.waitFor(1);
  await notify([msg(A, 1101)]);
  await w8.waitFor(2);
  eq(w8.json().map((f) => f.replay ?? f.seq), [true, 1101], "since = resync seq: replay:true, then live");

  console.log("client frames");
  w8.ws.send("ping");
  await w8.waitFor(3);
  eq(w8.frames[2], "pong", "ping answered pong");
  w8.ws.send("hello");
  const c = await w8.waitClose();
  eq(c?.code, 1008, "any other client frame closes with 1008");
  for (const w of [w1, w2, w3, w4, w5, w6, w7]) w.close();

  console.log("buffer pruning");
  const B = DAO(rnd());
  for (let p = 0; p < 26; p++) {
    const r = await notify(Array.from({ length: 20 }, (_, i) => msg(B, p * 20 + i + 1)));
    if (r.status !== 200) { ok(false, `prune POST ${p}: ${r.status} ${r.text}`); break; }
  }
  const b1 = connect(B, 1);
  await b1.waitFor(1);
  eq(b1.json()[0].replay, false, "520 messages: since=1 was pruned (500-row cap), replay:false");
  const b2 = connect(B, 20);
  await b2.waitFor(501, 15000);
  await sleep(300);
  const bf = b2.json();
  eq([bf[0].replay, bf.length - 1, bf[1]?.seq, bf.at(-1)?.seq], [true, 500, 21, 520], "since=20 (new continuous_since) replays the 500 kept rows");
  b1.close(); b2.close();

  const C = DAO(rnd());
  for (let p = 0; p < 10; p++) {
    const r = await notify([bigMessage(C, p * 2 + 1, 250 * 1024), bigMessage(C, p * 2 + 2, 250 * 1024)]);
    if (r.status !== 200) { ok(false, `4 MB POST ${p}: ${r.status} ${r.text}`); break; }
  }
  const c1 = connect(C, 0);
  await c1.waitFor(1);
  eq(c1.json()[0].replay, false, "20 x 250 KB: oldest rows pruned by the 4 MB cap, replay:false");
  const c2 = connect(C, 10);
  await c2.waitFor(11, 15000);
  await sleep(300);
  eq([c2.json()[0].replay, c2.json().slice(1).map((f) => f.seq)], [true, [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]], "recent rows still replay under the 4 MB cap");
  c1.close(); c2.close();

  console.log("never-notified channels leave no storage");
  if (EXTERNAL) {
    console.log("  skip (E2E_EXTERNAL: persist dir unknown)");
  } else {
    await sleep(500);
    const before = snapshot(PERSIST);
    const ghosts = [DAO(rnd()), DAO(rnd()), "net:Ghost-" + rnd()];
    for (const [i, g] of ghosts.entries()) {
      const w = connect(g, i === 0 ? undefined : 7);
      await w.waitFor(1);
      eq(w.json()[0], { t: "hello", channel: g, seq: 0, head: null, replay: false }, `hello for never-notified ${g.slice(0, 24)}...`);
      w.ws.send("ping");
      await w.waitFor(2);
      w.close();
    }
    await sleep(1500);
    const after = snapshot(PERSIST);
    const added = Object.keys(after).filter((p) => !(p in before));
    const changed = Object.keys(after).filter((p) => p in before && before[p] !== after[p]);
    const sqlite = added.filter((p) => p.endsWith(".sqlite"));
    const withTables = sqlite.filter((p) => userTables(p).length > 0);
    console.log(`  (files added: ${added.length}, changed: ${changed.length}, sqlite files with tables: ${withTables.length})`);
    for (const p of added) console.log(`    + ${p.slice(PERSIST.length)} ${after[p]}`);
    ok(sqlite.length === ghosts.length, `one simulator file per object (${sqlite.length}), holding only miniflare's own table`);
    ok(withTables.length === 0, "no Worker tables or rows created for never-notified channels");
    ok(changed.every((p) => !p.endsWith(".sqlite")), "no existing object database was modified");
  }

  console.log("CPU");
  const D1 = DAO(rnd()), D2 = DAO(rnd());
  const times = [];
  let size = 0;
  for (let i = 1; i <= 10; i++) {
    const body = JSON.stringify({ messages: [bigMessage(D1, i, 255 * 1024), bigMessage(D2, i, 255 * 1024)] });
    size = Buffer.byteLength(body);
    const t = Date.now();
    const r = await postRaw(body);
    times.push(Date.now() - t);
    if (r.status !== 200) ok(false, `512 KB POST ${i}: ${r.status} ${r.text}`);
  }
  ok(size > 500 * 1024 && size <= 512 * 1024, `body size ${size} bytes, 2 messages`);
  console.log(`  wall time per POST (ms): ${times.join(" ")}`);
  ok(!/exceeded|cpu limit|Worker exceeded/i.test(output), "no CPU-limit errors in wrangler output");
}

try {
  await main();
} catch (e) {
  failures++;
  console.error(e);
} finally {
  stopWrangler();
  if (!EXTERNAL) {
    await sleep(1000);
    try { rmSync(PERSIST, { recursive: true, force: true }); } catch {}
  }
  if (failures && output) console.log("--- wrangler output ---\n" + output.slice(-4000));
  console.log(`\n${passes} passed, ${failures} failed`);
  process.exitCode = failures ? 1 : 0;
}
