// Unit tests for the notify auth and validation code (node --experimental-strip-types --test).
import { test } from "node:test";
import assert from "node:assert/strict";
import { sign, verifyNotify, MAX_BODY_BYTES } from "../src/auth.ts";
import { parseNotifyBody, parseSince, isValidChannel, BadRequest, MAX_MESSAGE_BYTES } from "../src/validate.ts";
import { bigMessage, DAO, signNode } from "./helpers.mjs";

const enc = new TextEncoder();
const VECTOR = "v1=8cc09b05dd38fe0709b20698d82926fdf629b028c26ed77bd47929dff3df2a15";

test("HMAC test vector from CONTRACT.md section 6", async () => {
  assert.equal(await sign("test-secret", "1730000000", '{"messages":[]}'), VECTOR);
  assert.equal(signNode("test-secret", "1730000000", '{"messages":[]}'), VECTOR);
  const r = await verifyNotify(enc.encode('{"messages":[]}'), "1730000000", VECTOR, ["test-secret"], 1730000000);
  assert.deepEqual(r, { ok: true });
});

test("rotation: NOTIFY_SECRET_PREV is accepted, unknown secrets are not", async () => {
  const body = enc.encode('{"messages":[]}');
  const ts = "1730000000";
  const byPrev = await sign("old", ts, body);
  assert.deepEqual(await verifyNotify(body, ts, byPrev, ["new", "old"], 1730000000), { ok: true });
  assert.deepEqual(await verifyNotify(body, ts, byPrev, ["new", undefined], 1730000000), {
    ok: false, status: 401, error: "bad signature",
  });
});

test("bad signatures are 401", async () => {
  const body = enc.encode('{"messages":[]}');
  const ts = "1730000000";
  const flip = VECTOR.slice(0, -1) + (VECTOR.endsWith("5") ? "6" : "5");
  for (const sig of [null, "", flip, VECTOR.toUpperCase(), VECTOR.slice(3), "v2=" + VECTOR.slice(3), VECTOR + "00"]) {
    const r = await verifyNotify(body, ts, sig, ["test-secret"], 1730000000);
    assert.equal(r.ok, false, String(sig));
    assert.equal(r.status, 401);
  }
  // Body tampered after signing.
  const r = await verifyNotify(enc.encode('{"messages":[ ]}'), ts, VECTOR, ["test-secret"], 1730000000);
  assert.equal(r.status, 401);
});

test("replay window: 300 s accepted, 301 s rejected, both directions", async () => {
  const body = enc.encode('{"messages":[]}');
  const ts = "1730000000";
  for (const [now, ok] of [[1730000300, true], [1729999700, true], [1730000301, false], [1729999699, false]]) {
    const r = await verifyNotify(body, ts, VECTOR, ["test-secret"], now);
    assert.equal(r.ok, ok, `now=${now}`);
  }
  for (const bad of [null, "", "abc", "1730000000.5", "-1"]) {
    assert.equal((await verifyNotify(body, bad, VECTOR, ["test-secret"], 1730000000)).status, 401);
  }
});

test("body over 512 KB is 413 before any HMAC work", async () => {
  const body = new Uint8Array(MAX_BODY_BYTES + 1);
  const r = await verifyNotify(body, null, null, ["s"], 0);
  assert.deepEqual(r, { ok: false, status: 413, error: "body too large" });
});

test("message validation", () => {
  const ok = { channel: DAO(1), seq: 5, kind: "changes", head: "h", counts: { proposals: 1 }, changes: [], extra: { a: 1 } };
  const p = parseNotifyBody(JSON.stringify({ messages: [ok, { channel: "net:Localhost", seq: 9, kind: "resync", head: null }] }));
  const m = p.groups.get(DAO(1))[0];
  assert.equal(m.counts, '{"proposals":1}');
  // Frame: t first, every field (unknown ones too) copied unchanged.
  assert.deepEqual(JSON.parse(m.frame), { t: "changes", ...ok });
  assert.ok(m.frame.startsWith('{"t":"changes",'));
  assert.equal(m.size, enc.encode(m.frame).byteLength);
  assert.deepEqual(p.order, [DAO(1), "net:Localhost"]);
  assert.equal(p.groups.get("net:Localhost")[0].counts, null);

  const bad = (msgs, status = 400) => {
    assert.throws(() => parseNotifyBody(JSON.stringify({ messages: msgs })), (e) => e instanceof BadRequest && e.status === status);
  };
  bad([{ ...ok, channel: "dao:Localhost:0xABC" }]);
  bad([{ ...ok, channel: `dao:Localhost:0x${"A".repeat(40)}` }]);
  bad([{ ...ok, seq: 0 }]);
  bad([{ ...ok, seq: 2 ** 53 }]);
  bad([{ ...ok, seq: 1.5 }]);
  bad([{ ...ok, kind: "head" }]);
  bad([{ ...ok, head: undefined }]);
  bad([{ ...ok, changes: undefined }]);
  bad([{ ...ok, kind: "resync" }]);
  bad([ok, { ...ok, seq: 5 }]);
  bad(Array.from({ length: 21 }, (_, i) => ({ ...ok, seq: i + 1 })), 413);
  bad([bigMessage(DAO(1), 1, MAX_MESSAGE_BYTES + 100)], 413);
  assert.throws(() => parseNotifyBody("not json"), BadRequest);
  assert.equal(parseNotifyBody('{"messages":[]}').order.length, 0);
});

test("channel and since parsing", () => {
  assert.ok(isValidChannel("net:Etherlink-Shadownet"));
  assert.ok(isValidChannel(DAO(255)));
  assert.ok(!isValidChannel("net:" + "a".repeat(65)));
  assert.ok(!isValidChannel("net:a_b"));
  assert.equal(parseSince(null), null);
  assert.equal(parseSince("0"), 0);
  assert.equal(parseSince("1761998400123"), 1761998400123);
  assert.equal(parseSince("-1"), undefined);
  assert.equal(parseSince("1e3"), undefined);
  assert.equal(parseSince("9007199254740992"), undefined);
});

test("CPU: a 512 KB body with 2 messages verifies and parses well under 10 ms", async () => {
  const msgs = [bigMessage(DAO(1), 1, 255 * 1024), bigMessage(DAO(2), 1, 255 * 1024)];
  const text = JSON.stringify({ messages: msgs });
  const body = enc.encode(text);
  assert.ok(body.byteLength <= MAX_BODY_BYTES && body.byteLength > 500 * 1024, `size ${body.byteLength}`);
  const ts = "1730000000";
  const sig = signNode("s", ts, body);
  const run = async () => {
    const r = await verifyNotify(body, ts, sig, ["s"], 1730000000);
    assert.ok(r.ok);
    parseNotifyBody(new TextDecoder().decode(body));
  };
  for (let i = 0; i < 20; i++) await run(); // warm up the JIT, as a warm isolate would be
  const N = 50;
  const t0 = process.cpuUsage();
  for (let i = 0; i < N; i++) await run();
  const d = process.cpuUsage(t0);
  const ms = (d.user + d.system) / 1000 / N;
  console.log(`# verify + parse + validate of ${body.byteLength} bytes: ${ms.toFixed(2)} ms CPU per request`);
  assert.ok(ms < 5, `${ms} ms`);
});
