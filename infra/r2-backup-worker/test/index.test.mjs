import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import worker, {
  authorize,
  validateMetadata,
  validateParts,
} from "../src/index.js";

const ORIGIN = "https://backup.invalid";
const TOKEN = "test-only-backup-token-0123456789-ABCDEFGHX";
const PART_BYTES = 8 * 1024 * 1024;

const BASE_METADATA = Object.freeze({
  format: "presence-monitor-sqlite-backup/v1",
  created_at: "2026-08-27T16:00:00.000000+00:00",
  instance_id: "production",
  tier: "hourly",
  database_bytes: 23,
  database_sha256: "a".repeat(64),
  gzip_bytes: 11,
  gzip_sha256: "b".repeat(64),
  schema_version: 1,
});

const EXPECTED_KEY =
  `backups/production/hourly/20260827T160000.000000Z-${"b".repeat(64)}.sqlite3.gz`;

function metadata(overrides = {}) {
  return { ...BASE_METADATA, ...overrides };
}

function request(path, init = {}, token = TOKEN) {
  const headers = new Headers(init.headers);
  if (token !== null) {
    headers.set("authorization", `Bearer ${token}`);
  }
  return new Request(`${ORIGIN}${path}`, { ...init, headers });
}

function jsonRequest(path, body, init = {}, token = TOKEN) {
  const serialized = typeof body === "string" ? body : JSON.stringify(body);
  return request(
    path,
    {
      method: "POST",
      ...init,
      headers: {
        "content-type": "application/json",
        ...init.headers,
      },
      body: serialized,
    },
    token,
  );
}

async function json(response) {
  return JSON.parse(await response.text());
}

async function readBytes(body) {
  if (body instanceof Uint8Array) {
    return body;
  }
  if (body instanceof ArrayBuffer) {
    return new Uint8Array(body);
  }
  if (typeof body === "string") {
    return new TextEncoder().encode(body);
  }
  if (!body || typeof body.getReader !== "function") {
    throw new TypeError("body is not a readable stream");
  }

  const chunks = [];
  let size = 0;
  const reader = body.getReader();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
    chunks.push(chunk);
    size += chunk.byteLength;
  }
  const combined = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return combined;
}

function md5(bytes) {
  return createHash("md5").update(bytes).digest("hex");
}

class InMemoryR2Bucket {
  constructor({ pageSize = 2 } = {}) {
    this.objects = new Map();
    this.uploads = new Map();
    this.pageSize = pageSize;
    this.nextUpload = 1;
    this.deleteCalls = 0;
    this.listCalls = [];
  }

  _object(key, bytes, options = {}) {
    const etag = md5(bytes);
    return {
      key,
      size: bytes.byteLength,
      etag,
      httpEtag: `"${etag}"`,
      uploaded: new Date("2026-08-27T16:01:00.000Z"),
      httpMetadata: { ...(options.httpMetadata ?? {}) },
      customMetadata: { ...(options.customMetadata ?? {}) },
      bytes: new Uint8Array(bytes),
    };
  }

  seed(key, bytes, options = {}) {
    const value = bytes instanceof Uint8Array ? bytes : new TextEncoder().encode(bytes);
    this.objects.set(key, this._object(key, value, options));
  }

  _head(object) {
    if (!object) return null;
    const { bytes: _bytes, ...head } = object;
    return { ...head, customMetadata: { ...head.customMetadata } };
  }

  async head(key) {
    return this._head(this.objects.get(key));
  }

  async get(key) {
    const object = this.objects.get(key);
    if (!object) return null;
    const bytes = new Uint8Array(object.bytes);
    return {
      ...this._head(object),
      body: new ReadableStream({
        start(controller) {
          controller.enqueue(bytes);
          controller.close();
        },
      }),
    };
  }

  async list(options = {}) {
    this.listCalls.push({ ...options });
    const all = [...this.objects.values()]
      .filter((object) => object.key.startsWith(options.prefix ?? ""))
      .sort((left, right) => left.key.localeCompare(right.key));
    const offset = options.cursor ? Number(options.cursor) : 0;
    const end = Math.min(offset + this.pageSize, all.length);
    return {
      objects: all.slice(offset, end).map((object) => this._head(object)),
      truncated: end < all.length,
      cursor: end < all.length ? String(end) : undefined,
    };
  }

  async createMultipartUpload(key, options = {}) {
    const uploadId = `upload-${this.nextUpload++}`;
    this.uploads.set(uploadId, { key, options, parts: new Map() });
    return this._multipart(key, uploadId);
  }

  resumeMultipartUpload(key, uploadId) {
    return this._multipart(key, uploadId);
  }

  _multipart(key, uploadId) {
    const bucket = this;
    const current = () => {
      const upload = bucket.uploads.get(uploadId);
      if (!upload || upload.key !== key) {
        const error = new Error("No such multipart upload");
        error.status = 404;
        throw error;
      }
      return upload;
    };

    return {
      key,
      uploadId,
      async uploadPart(partNumber, body) {
        const upload = current();
        const bytes = await readBytes(body);
        const etag = createHash("sha512").update(bytes).digest("base64url");
        upload.parts.set(partNumber, { bytes, etag, partNumber });
        return { partNumber, etag };
      },
      async complete(parts) {
        const upload = current();
        const chunks = parts.map((part) => {
          const stored = upload.parts.get(part.partNumber);
          if (!stored || stored.etag !== part.etag) {
            const error = new Error("Invalid multipart part");
            error.status = 400;
            throw error;
          }
          return stored.bytes;
        });
        const total = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
        const bytes = new Uint8Array(total);
        let offset = 0;
        for (const chunk of chunks) {
          bytes.set(chunk, offset);
          offset += chunk.byteLength;
        }
        const object = bucket._object(key, bytes, upload.options);
        bucket.objects.set(key, object);
        bucket.uploads.delete(uploadId);
        return bucket._head(object);
      },
      async abort() {
        current();
        bucket.uploads.delete(uploadId);
      },
    };
  }

  async delete() {
    this.deleteCalls += 1;
    throw new Error("delete must never be called");
  }
}

function environment(bucket = new InMemoryR2Bucket(), token = TOKEN) {
  return { BACKUPS: bucket, BACKUP_TOKEN: token };
}

function storedOptions(value = BASE_METADATA) {
  return {
    httpMetadata: {
      contentType: "application/gzip",
      cacheControl: "no-store",
    },
    customMetadata: Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, String(item)]),
    ),
  };
}

async function createUpload(env, value = metadata()) {
  const response = await worker.fetch(
    jsonRequest("/v1/uploads", value),
    env,
  );
  return { response, body: await json(response) };
}

async function uploadOnePart(env, uploadId, key, bytes = new TextEncoder().encode("hello world")) {
  const response = await worker.fetch(
    request(
      `/v1/uploads/${encodeURIComponent(uploadId)}/parts/1?key=${encodeURIComponent(key)}`,
      {
        method: "PUT",
        headers: {
          "content-length": String(bytes.byteLength),
          "content-type": "application/octet-stream",
        },
        body: bytes,
      },
    ),
    env,
  );
  return { response, body: await json(response) };
}

test("authorize hashes and compares valid bearer values without accepting malformed secrets", async () => {
  assert.equal(await authorize(new Request(ORIGIN), TOKEN), false);
  assert.equal(
    await authorize(request("/v1/objects?key=x"), TOKEN),
    true,
  );
  assert.equal(
    await authorize(request("/v1/objects?key=x", {}, `${TOKEN}x`), TOKEN),
    false,
  );
  assert.equal(await authorize(request("/", {}, TOKEN), "too-short"), false);
  assert.equal(
    await authorize(
      request("/", { headers: { authorization: `Bearer ${"x".repeat(513)}` } }, null),
      TOKEN,
    ),
    false,
  );
});

test("missing and wrong bearer credentials return identical generic 401 responses", async () => {
  const env = environment();
  const missing = await worker.fetch(
    request(`/v1/objects?key=${encodeURIComponent(EXPECTED_KEY)}`, {}, null),
    env,
  );
  const wrong = await worker.fetch(
    request(`/v1/objects?key=${encodeURIComponent(EXPECTED_KEY)}`, {}, "x".repeat(TOKEN.length)),
    env,
  );

  assert.equal(missing.status, 401);
  assert.equal(wrong.status, 401);
  assert.equal(await missing.text(), await wrong.text());
  assert.equal(missing.headers.get("content-length"), wrong.headers.get("content-length"));
  assert.equal(missing.headers.get("x-content-type-options"), "nosniff");
  assert.equal(missing.headers.get("access-control-allow-origin"), null);
});

test("validateMetadata derives a canonical content-addressed key", () => {
  const result = validateMetadata(metadata());
  assert.equal(result.key, EXPECTED_KEY);
  assert.deepEqual(result.metadata, metadata());
});

test("metadata validation rejects malformed instances, tiers, timestamps, sizes, digests, and fields", () => {
  const invalid = [
    metadata({ instance_id: "../production" }),
    metadata({ instance_id: "Production" }),
    metadata({ tier: "weekly" }),
    metadata({ created_at: "2026-02-30T16:00:00.000000+00:00" }),
    metadata({ created_at: "2026-08-27T16:00:00Z" }),
    metadata({ gzip_bytes: 0 }),
    metadata({ gzip_bytes: PART_BYTES * 10_000 + 1 }),
    metadata({ database_sha256: "A".repeat(64) }),
    metadata({ gzip_sha256: "not-a-digest" }),
    metadata({ schema_version: 0 }),
    metadata({ format: "presence-monitor-sqlite-backup/v2" }),
    { ...metadata(), unexpected: true },
  ];

  for (const value of invalid) {
    assert.throws(() => validateMetadata(value));
  }
});

test("create upload is authenticated, validates JSON, and stores private metadata", async () => {
  const bucket = new InMemoryR2Bucket();
  const env = environment(bucket);
  const { response, body } = await createUpload(env);

  assert.equal(response.status, 201);
  assert.equal(response.headers.get("access-control-allow-origin"), null);
  assert.equal(body.key, EXPECTED_KEY);
  assert.match(body.upload_id, /^upload-/);
  assert.equal(body.existing, false);
  const upload = bucket.uploads.get(body.upload_id);
  assert.equal(upload.key, EXPECTED_KEY);
  assert.deepEqual(upload.options.httpMetadata, {
    contentType: "application/gzip",
    cacheControl: "no-store",
  });
  assert.equal(upload.options.customMetadata.gzip_sha256, BASE_METADATA.gzip_sha256);
  assert.equal(upload.options.customMetadata.gzip_bytes, "11");
});

test("JSON routes reject unsupported media types, oversized bodies, and invalid JSON", async () => {
  const env = environment();
  const unsupported = await worker.fetch(
    request("/v1/uploads", { method: "POST", body: "{}" }),
    env,
  );
  assert.equal(unsupported.status, 415);

  const oversized = await worker.fetch(
    jsonRequest("/v1/uploads", `{"padding":"${"x".repeat(33 * 1024)}"}`),
    env,
  );
  assert.equal(oversized.status, 413);

  const malformed = await worker.fetch(
    jsonRequest("/v1/uploads", "{"),
    env,
  );
  assert.equal(malformed.status, 400);
});

test("part route streams a bounded part and returns the R2 part descriptor", async () => {
  const bucket = new InMemoryR2Bucket();
  const env = environment(bucket);
  const created = await createUpload(env);
  const uploaded = await uploadOnePart(env, created.body.upload_id, created.body.key);

  assert.equal(uploaded.response.status, 200);
  assert.equal(uploaded.body.part_number, 1);
  assert.match(uploaded.body.etag, /^[A-Za-z0-9_-]{86}$/);
  assert.equal(bucket.uploads.get(created.body.upload_id).parts.get(1).bytes.byteLength, 11);
});

test("part route rejects missing, invalid, mismatched, and oversized content lengths", async () => {
  const env = environment();
  const created = await createUpload(env);
  const path = `/v1/uploads/${created.body.upload_id}/parts/1?key=${encodeURIComponent(created.body.key)}`;

  for (const contentLength of [null, "0", "1.5", String(PART_BYTES + 1)]) {
    const headers = { "content-type": "application/octet-stream" };
    if (contentLength !== null) headers["content-length"] = contentLength;
    const response = await worker.fetch(
      request(path, { method: "PUT", headers, body: new Uint8Array([1]) }),
      env,
    );
    assert.equal(response.status, contentLength === String(PART_BYTES + 1) ? 413 : 400);
  }

  const mismatch = await worker.fetch(
    request(path, {
      method: "PUT",
      headers: {
        "content-length": "2",
        "content-type": "application/octet-stream",
      },
      body: new Uint8Array([1]),
    }),
    env,
  );
  assert.equal(mismatch.status, 400);
});

test("part route rejects invalid upload IDs, keys, part numbers, and content types", async () => {
  const env = environment();
  const cases = [
    `/v1/uploads/%20/parts/1?key=${encodeURIComponent(EXPECTED_KEY)}`,
    `/v1/uploads/upload-1/parts/0?key=${encodeURIComponent(EXPECTED_KEY)}`,
    `/v1/uploads/upload-1/parts/10001?key=${encodeURIComponent(EXPECTED_KEY)}`,
    "/v1/uploads/upload-1/parts/1?key=../secret",
  ];
  for (const path of cases) {
    const response = await worker.fetch(
      request(path, {
        method: "PUT",
        headers: {
          "content-length": "1",
          "content-type": "application/octet-stream",
        },
        body: new Uint8Array([1]),
      }),
      env,
    );
    assert.equal(response.status, 400);
  }

  const media = await worker.fetch(
    request(`/v1/uploads/upload-1/parts/1?key=${encodeURIComponent(EXPECTED_KEY)}`, {
      method: "PUT",
      headers: { "content-length": "1", "content-type": "text/plain" },
      body: new Uint8Array([1]),
    }),
    env,
  );
  assert.equal(media.status, 415);
});

test("validateParts accepts only sorted, unique, bounded R2 part descriptors", () => {
  assert.deepEqual(
    validateParts({ parts: [{ part_number: 1, etag: "a".repeat(32) }] }),
    [{ partNumber: 1, etag: "a".repeat(32) }],
  );
  assert.deepEqual(
    validateParts({ parts: [{ part_number: 1, etag: "A".repeat(171) }] }),
    [{ partNumber: 1, etag: "A".repeat(171) }],
  );

  const invalid = [
    {},
    { parts: [] },
    { parts: [{ part_number: 2, etag: "a".repeat(32) }, { part_number: 1, etag: "b".repeat(32) }] },
    { parts: [{ part_number: 1, etag: "a".repeat(32) }, { part_number: 1, etag: "b".repeat(32) }] },
    { parts: [{ part_number: 0, etag: "a".repeat(32) }] },
    { parts: [{ part_number: 1, etag: "etag with spaces" }] },
    { parts: [{ part_number: 1, etag: "a".repeat(257) }] },
    { parts: [{ part_number: 1, etag: "a".repeat(32), extra: true }] },
  ];
  for (const value of invalid) {
    assert.throws(() => validateParts(value));
  }
});

test("multipart completion creates an immutable object and is idempotent", async () => {
  const bucket = new InMemoryR2Bucket();
  const env = environment(bucket);
  const created = await createUpload(env);
  const part = await uploadOnePart(env, created.body.upload_id, created.body.key);
  const completionRequest = () => jsonRequest(
    `/v1/uploads/${created.body.upload_id}/complete?key=${encodeURIComponent(created.body.key)}`,
    { parts: [part.body] },
  );

  const completed = await worker.fetch(completionRequest(), env);
  assert.equal(completed.status, 200);
  const completedBody = await json(completed);
  assert.equal(completedBody.key, EXPECTED_KEY);
  assert.equal(completedBody.gzip_bytes, 11);
  assert.equal(completedBody.gzip_sha256, BASE_METADATA.gzip_sha256);
  assert.deepEqual(Object.keys(completedBody).sort(), [...Object.keys(BASE_METADATA), "key"].sort());

  const retried = await worker.fetch(completionRequest(), env);
  assert.equal(retried.status, 200);
  assert.deepEqual(await json(retried), completedBody);

  const recreated = await createUpload(env);
  assert.equal(recreated.response.status, 200);
  assert.deepEqual(recreated.body, {
    key: EXPECTED_KEY,
    upload_id: null,
    existing: true,
  });
  assert.equal(bucket.uploads.size, 0);
  assert.equal(bucket.deleteCalls, 0);
});

test("existing conflicting objects fail closed on create and completion", async () => {
  const bucket = new InMemoryR2Bucket();
  bucket.seed(EXPECTED_KEY, "wrong-size", storedOptions());
  const env = environment(bucket);

  const created = await createUpload(env);
  assert.equal(created.response.status, 409);

  const completed = await worker.fetch(
    jsonRequest(
      `/v1/uploads/upload-does-not-matter/complete?key=${encodeURIComponent(EXPECTED_KEY)}`,
      { parts: [{ part_number: 1, etag: "a".repeat(32) }] },
    ),
    env,
  );
  assert.equal(completed.status, 409);
  assert.equal(bucket.deleteCalls, 0);
});

test("unknown multipart uploads return generic 404 for part, complete, and abort", async () => {
  const env = environment();
  const key = encodeURIComponent(EXPECTED_KEY);
  const part = await worker.fetch(
    request(`/v1/uploads/missing-upload/parts/1?key=${key}`, {
      method: "PUT",
      headers: {
        "content-length": "1",
        "content-type": "application/octet-stream",
      },
      body: new Uint8Array([1]),
    }),
    env,
  );
  const complete = await worker.fetch(
    jsonRequest(`/v1/uploads/missing-upload/complete?key=${key}`, {
      parts: [{ part_number: 1, etag: "a".repeat(32) }],
    }),
    env,
  );
  const abort = await worker.fetch(
    request(`/v1/uploads/missing-upload/abort?key=${key}`, { method: "POST" }),
    env,
  );

  for (const response of [part, complete, abort]) {
    assert.equal(response.status, 404);
    const body = await response.text();
    assert.doesNotMatch(body, /missing-upload|backups\/production/);
  }
});

test("abort removes an incomplete upload without exposing a deletion route", async () => {
  const bucket = new InMemoryR2Bucket();
  const env = environment(bucket);
  const created = await createUpload(env);
  const response = await worker.fetch(
    request(
      `/v1/uploads/${created.body.upload_id}/abort?key=${encodeURIComponent(created.body.key)}`,
      { method: "POST" },
    ),
    env,
  );

  assert.equal(response.status, 204);
  assert.equal(bucket.uploads.size, 0);
  assert.equal(bucket.deleteCalls, 0);
  const deleteResponse = await worker.fetch(
    request(`/v1/objects?key=${encodeURIComponent(EXPECTED_KEY)}`, { method: "DELETE" }),
    env,
  );
  assert.equal(deleteResponse.status, 405);
  assert.equal(bucket.deleteCalls, 0);
});

test("authenticated HEAD and GET stream an exact object with safe metadata headers", async () => {
  const bucket = new InMemoryR2Bucket();
  bucket.seed(EXPECTED_KEY, "hello world", storedOptions());
  const env = environment(bucket);
  const path = `/v1/objects?key=${encodeURIComponent(EXPECTED_KEY)}`;

  const head = await worker.fetch(request(path, { method: "HEAD" }), env);
  assert.equal(head.status, 200);
  assert.equal(head.headers.get("content-length"), "11");
  assert.equal(head.headers.get("content-type"), "application/gzip");
  assert.equal(head.headers.get("cache-control"), "no-store");
  assert.equal(head.headers.get("x-backup-format"), BASE_METADATA.format);
  assert.equal(head.headers.get("x-backup-created-at"), BASE_METADATA.created_at);
  assert.equal(head.headers.get("x-backup-instance-id"), BASE_METADATA.instance_id);
  assert.equal(head.headers.get("x-backup-database-bytes"), String(BASE_METADATA.database_bytes));
  assert.equal(head.headers.get("x-backup-database-sha256"), BASE_METADATA.database_sha256);
  assert.equal(head.headers.get("x-backup-gzip-bytes"), String(BASE_METADATA.gzip_bytes));
  assert.equal(head.headers.get("x-backup-gzip-sha256"), BASE_METADATA.gzip_sha256);
  assert.equal(head.headers.get("x-backup-schema-version"), String(BASE_METADATA.schema_version));
  assert.equal(head.headers.get("x-backup-key"), EXPECTED_KEY);
  assert.match(head.headers.get("etag"), /^"[0-9a-f]{32}"$/);
  assert.match(head.headers.get("content-disposition"), /^attachment; filename="[^"]+"$/);
  assert.equal(await head.text(), "");

  const get = await worker.fetch(request(path), env);
  assert.equal(get.status, 200);
  assert.equal(await get.text(), "hello world");
  assert.equal(get.headers.get("access-control-allow-origin"), null);

  const missing = await worker.fetch(
    request(
      `/v1/objects?key=${encodeURIComponent(EXPECTED_KEY.replace("b".repeat(64), "c".repeat(64)))}`,
    ),
    env,
  );
  assert.equal(missing.status, 404);
});

test("exact object routes reject malformed, duplicate, and extra key parameters", async () => {
  const env = environment();
  const paths = [
    "/v1/objects?key=../secret",
    `/v1/objects?key=${encodeURIComponent(EXPECTED_KEY)}&key=${encodeURIComponent(EXPECTED_KEY)}`,
    `/v1/objects?key=${encodeURIComponent(EXPECTED_KEY)}&extra=1`,
  ];
  for (const path of paths) {
    const response = await worker.fetch(request(path), env);
    assert.equal(response.status, 400);
  }
});

test("latest follows every cursor and returns metadata for the final lexicographic key", async () => {
  const bucket = new InMemoryR2Bucket({ pageSize: 1 });
  const first = metadata({
    created_at: "2026-08-27T15:00:00.000000+00:00",
    gzip_sha256: "1".repeat(64),
  });
  const second = metadata({
    created_at: "2026-08-27T17:00:00.000000+00:00",
    gzip_sha256: "2".repeat(64),
  });
  const firstValidated = validateMetadata(first);
  const secondValidated = validateMetadata(second);
  bucket.seed(firstValidated.key, new Uint8Array(first.gzip_bytes), storedOptions(first));
  bucket.seed(secondValidated.key, new Uint8Array(second.gzip_bytes), storedOptions(second));
  bucket.seed(
    validateMetadata(metadata({ tier: "daily", gzip_sha256: "3".repeat(64) })).key,
    new Uint8Array(BASE_METADATA.gzip_bytes),
    storedOptions(metadata({ tier: "daily", gzip_sha256: "3".repeat(64) })),
  );
  const env = environment(bucket);

  const response = await worker.fetch(
    request("/v1/latest?instance=production&tier=hourly"),
    env,
  );
  const body = await json(response);
  assert.equal(response.status, 200);
  assert.equal(body.key, secondValidated.key);
  assert.equal(body.gzip_sha256, second.gzip_sha256);
  assert.equal(body.tier, "hourly");
  assert.deepEqual(Object.keys(body).sort(), [...Object.keys(BASE_METADATA), "key"].sort());
  assert.equal(bucket.listCalls.length, 2);
  assert.deepEqual(bucket.listCalls[0].include, ["customMetadata"]);
});

test("latest validates scope and returns generic 404 when no object exists", async () => {
  const env = environment();
  const invalid = [
    "/v1/latest?instance=../production&tier=hourly",
    "/v1/latest?instance=production&tier=weekly",
    "/v1/latest?instance=production&tier=hourly&extra=1",
    "/v1/latest?instance=production&instance=other&tier=hourly",
  ];
  for (const path of invalid) {
    const response = await worker.fetch(request(path), env);
    assert.equal(response.status, 400);
  }
  const missing = await worker.fetch(
    request("/v1/latest?instance=production&tier=hourly"),
    env,
  );
  assert.equal(missing.status, 404);
});

test("route method allow-lists are explicit and no response enables CORS", async () => {
  const env = environment();
  const cases = [
    ["/healthz", "POST", "GET"],
    ["/v1/uploads", "GET", "POST"],
    [`/v1/objects?key=${encodeURIComponent(EXPECTED_KEY)}`, "POST", "GET, HEAD"],
    ["/v1/latest?instance=production&tier=hourly", "HEAD", "GET"],
  ];

  for (const [path, method, allow] of cases) {
    const response = await worker.fetch(request(path, { method }), env);
    assert.equal(response.status, 405);
    assert.equal(response.headers.get("allow"), allow);
    assert.equal(response.headers.get("access-control-allow-origin"), null);
  }

  const options = await worker.fetch(
    request("/v1/uploads", { method: "OPTIONS" }),
    env,
  );
  assert.equal(options.status, 405);
  assert.equal(options.headers.get("access-control-allow-origin"), null);
});

test("health is public, minimal, non-cacheable, and unknown routes stay generic", async () => {
  const env = environment();
  const health = await worker.fetch(new Request(`${ORIGIN}/healthz`), env);
  assert.equal(health.status, 200);
  assert.deepEqual(await json(health), { status: "ok" });
  assert.equal(health.headers.get("cache-control"), "no-store");
  assert.equal(health.headers.get("access-control-allow-origin"), null);

  const missing = await worker.fetch(new Request(`${ORIGIN}/not-found`), env);
  assert.equal(missing.status, 404);
  assert.deepEqual(await json(missing), { error: "not_found" });
});

test("toolchain, binding, lifecycle, and append-only source policy stay pinned", () => {
  const packageJson = JSON.parse(
    readFileSync(new URL("../package.json", import.meta.url), "utf8"),
  );
  assert.deepEqual(packageJson.scripts, {
    check: "node --check src/index.js",
    test: "node --test test/*.test.mjs",
    deploy: "wrangler deploy",
  });
  assert.deepEqual(packageJson.devDependencies, { wrangler: "4.127.0" });

  const wrangler = JSON.parse(
    readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8"),
  );
  assert.equal(wrangler.workers_dev, true);
  assert.deepEqual(wrangler.r2_buckets, [
    { binding: "BACKUPS", bucket_name: "presence-monitor-backups" },
  ]);
  assert.equal("account_id" in wrangler, false);
  assert.equal("vars" in wrangler, false);

  const lifecycle = JSON.parse(
    readFileSync(new URL("../lifecycle.json", import.meta.url), "utf8"),
  );
  assert.deepEqual(
    lifecycle.rules.map((rule) => ({
      enabled: rule.enabled,
      prefix: rule.conditions.prefix,
      maxAge: rule.deleteObjectsTransition.condition.maxAge,
      type: rule.deleteObjectsTransition.condition.type,
    })),
    [
      { enabled: true, prefix: "backups/production/hourly/", maxAge: 8 * 86_400, type: "Age" },
      { enabled: true, prefix: "backups/production/daily/", maxAge: 93 * 86_400, type: "Age" },
      { enabled: true, prefix: "backups/production/monthly/", maxAge: 400 * 86_400, type: "Age" },
    ],
  );

  const source = readFileSync(new URL("../src/index.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /\.delete\s*\(/);
  assert.doesNotMatch(source, /access-control-allow-origin/i);
});
