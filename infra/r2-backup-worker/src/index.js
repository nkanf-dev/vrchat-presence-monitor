const BACKUP_FORMAT = "presence-monitor-sqlite-backup/v1";
export const MULTIPART_LIMITS = Object.freeze({
  createJsonBytes: 32 * 1024,
  completeJsonBytes: 4 * 1024 * 1024,
  partBytes: 8 * 1024 * 1024,
  maxParts: 10_000,
  maxBackupBytes: 64 * 1024 ** 3,
});
const {
  createJsonBytes: CREATE_JSON_BYTES,
  completeJsonBytes: COMPLETE_JSON_BYTES,
  partBytes: PART_BYTES,
  maxParts: MAX_PARTS,
  maxBackupBytes: MAX_BACKUP_BYTES,
} = MULTIPART_LIMITS;
const MAX_GZIP_BYTES = MAX_BACKUP_BYTES;
const MAX_DATABASE_BYTES = MAX_BACKUP_BYTES;
const MAX_AUTHORIZATION_BYTES = 512;
const MIN_TOKEN_CHARACTERS = 43;
const MAX_ETAG_CHARACTERS = 256;
const MAX_PART_DESCRIPTOR_JSON_BYTES =
  '{"part_number":,"etag":""}'.length + String(MAX_PARTS).length + MAX_ETAG_CHARACTERS;
const MAX_COMPLETE_PAYLOAD_BYTES =
  '{"parts":[]}'.length +
  (MAX_PART_DESCRIPTOR_JSON_BYTES * MAX_PARTS) +
  (MAX_PARTS - 1);
if (
  MAX_BACKUP_BYTES > PART_BYTES * MAX_PARTS ||
  COMPLETE_JSON_BYTES < MAX_COMPLETE_PAYLOAD_BYTES
) {
  throw new Error("invalid multipart capacity limits");
}
const TIERS = new Set(["hourly", "daily", "monthly"]);
const METADATA_FIELDS = Object.freeze([
  "format",
  "created_at",
  "instance_id",
  "tier",
  "database_bytes",
  "database_sha256",
  "gzip_bytes",
  "gzip_sha256",
  "schema_version",
]);
const METADATA_FIELD_SET = new Set(METADATA_FIELDS);
const DIGEST_PATTERN = /^[0-9a-f]{64}$/;
const INSTANCE_PATTERN = /^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$/;
const UPLOAD_ID_PATTERN = /^[A-Za-z0-9._~+/=-]{1,1024}$/;
const ETAG_PATTERN = new RegExp(`^[A-Za-z0-9._~:+/=-]{1,${MAX_ETAG_CHARACTERS}}$`);
const KEY_PATTERN = new RegExp(
  "^backups/" +
    "([a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?)/" +
    "(hourly|daily|monthly)/" +
    "(\\d{8}T\\d{6}\\.\\d{6}Z)-" +
    "([0-9a-f]{64})\\.sqlite3\\.gz$",
);

const ERROR_CODES = Object.freeze({
  400: "invalid_request",
  401: "unauthorized",
  404: "not_found",
  405: "method_not_allowed",
  409: "conflict",
  413: "payload_too_large",
  415: "unsupported_media_type",
  500: "internal_error",
});

class HttpError extends Error {
  constructor(status) {
    super(ERROR_CODES[status] ?? "request_failed");
    this.name = "HttpError";
    this.status = status;
  }
}

function commonHeaders(initial = undefined) {
  const headers = new Headers(initial);
  headers.set("Cache-Control", "no-store");
  headers.set("X-Content-Type-Options", "nosniff");
  return headers;
}

function jsonResponse(value, status = 200, initialHeaders = undefined) {
  const body = JSON.stringify(value);
  const headers = commonHeaders(initialHeaders);
  headers.set("Content-Type", "application/json; charset=utf-8");
  headers.set("Content-Length", String(new TextEncoder().encode(body).byteLength));
  return new Response(body, { status, headers });
}

function errorResponse(status, initialHeaders = undefined) {
  return jsonResponse({ error: ERROR_CODES[status] ?? "request_failed" }, status, initialHeaders);
}

function methodNotAllowed(allow) {
  return errorResponse(405, { Allow: allow });
}

function hasExactKeys(value, expected) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const keys = Object.keys(value);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}

function requireInstance(value) {
  if (typeof value !== "string" || !INSTANCE_PATTERN.test(value)) {
    throw new HttpError(400);
  }
  return value;
}

function requireTier(value) {
  if (typeof value !== "string" || !TIERS.has(value)) {
    throw new HttpError(400);
  }
  return value;
}

function requireInteger(value, maximum) {
  if (!Number.isSafeInteger(value) || value <= 0 || value > maximum) {
    throw new HttpError(400);
  }
  return value;
}

function requireDigest(value) {
  if (typeof value !== "string" || !DIGEST_PATTERN.test(value)) {
    throw new HttpError(400);
  }
  return value;
}

function validUtcParts(parts) {
  const [year, month, day, hour, minute, second] = parts.map(Number);
  if (year < 2000 || year > 9999) return false;
  const date = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day &&
    date.getUTCHours() === hour &&
    date.getUTCMinutes() === minute &&
    date.getUTCSeconds() === second
  );
}

function parseCreatedAt(value) {
  if (typeof value !== "string") throw new HttpError(400);
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{6})\+00:00$/.exec(value);
  if (!match || !validUtcParts(match.slice(1, 7))) throw new HttpError(400);
  return {
    value,
    compact: `${match[1]}${match[2]}${match[3]}T${match[4]}${match[5]}${match[6]}.${match[7]}Z`,
  };
}

function parseCompactTimestamp(value) {
  const match = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})\.(\d{6})Z$/.exec(value);
  if (!match || !validUtcParts(match.slice(1, 7))) throw new HttpError(400);
  return `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}.${match[7]}+00:00`;
}

export function validateMetadata(value) {
  if (!hasExactKeys(value, METADATA_FIELD_SET)) throw new HttpError(400);
  if (value.format !== BACKUP_FORMAT) throw new HttpError(400);

  const created = parseCreatedAt(value.created_at);
  const normalized = {
    format: BACKUP_FORMAT,
    created_at: created.value,
    instance_id: requireInstance(value.instance_id),
    tier: requireTier(value.tier),
    database_bytes: requireInteger(value.database_bytes, MAX_DATABASE_BYTES),
    database_sha256: requireDigest(value.database_sha256),
    gzip_bytes: requireInteger(value.gzip_bytes, MAX_GZIP_BYTES),
    gzip_sha256: requireDigest(value.gzip_sha256),
    schema_version: requireInteger(value.schema_version, 2_147_483_647),
  };
  const key =
    `backups/${normalized.instance_id}/${normalized.tier}/` +
    `${created.compact}-${normalized.gzip_sha256}.sqlite3.gz`;
  return { key, metadata: normalized };
}

function validateKey(value) {
  if (typeof value !== "string" || value.length > 256) throw new HttpError(400);
  const match = KEY_PATTERN.exec(value);
  if (!match) throw new HttpError(400);
  const createdAt = parseCompactTimestamp(match[3]);
  return {
    key: value,
    instance_id: requireInstance(match[1]),
    tier: requireTier(match[2]),
    created_at: createdAt,
    gzip_sha256: requireDigest(match[4]),
  };
}

function validateUploadId(encodedValue) {
  let value;
  try {
    value = decodeURIComponent(encodedValue);
  } catch {
    throw new HttpError(400);
  }
  if (!UPLOAD_ID_PATTERN.test(value)) throw new HttpError(400);
  return value;
}

function validatePartNumber(value) {
  if (!/^[1-9]\d{0,4}$/.test(value)) throw new HttpError(400);
  const partNumber = Number(value);
  if (partNumber > MAX_PARTS) throw new HttpError(400);
  return partNumber;
}

export function validateParts(value) {
  if (!hasExactKeys(value, new Set(["parts"])) || !Array.isArray(value.parts)) {
    throw new HttpError(400);
  }
  if (value.parts.length === 0 || value.parts.length > MAX_PARTS) {
    throw new HttpError(400);
  }

  const normalized = [];
  let previous = 0;
  for (const part of value.parts) {
    if (!hasExactKeys(part, new Set(["part_number", "etag"]))) {
      throw new HttpError(400);
    }
    const partNumber = part.part_number;
    if (
      !Number.isInteger(partNumber) ||
      partNumber <= previous ||
      partNumber < 1 ||
      partNumber > MAX_PARTS ||
      typeof part.etag !== "string" ||
      !ETAG_PATTERN.test(part.etag)
    ) {
      throw new HttpError(400);
    }
    normalized.push({ partNumber, etag: part.etag });
    previous = partNumber;
  }
  return normalized;
}

function tokenShapeIsValid(value) {
  return (
    typeof value === "string" &&
    value.length >= MIN_TOKEN_CHARACTERS &&
    value.length <= MAX_AUTHORIZATION_BYTES &&
    /^[A-Za-z0-9._~+/=-]+$/.test(value)
  );
}

export async function authorize(request, configuredToken) {
  const authorization = request.headers.get("authorization") ?? "";
  if (authorization.length > MAX_AUTHORIZATION_BYTES) return false;

  const match = /^Bearer ([A-Za-z0-9._~+/=-]+)$/i.exec(authorization);
  const suppliedToken = match?.[1] ?? "";
  const storedToken = typeof configuredToken === "string" ? configuredToken : "";
  const encoder = new TextEncoder();
  const [suppliedHash, storedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(suppliedToken)),
    crypto.subtle.digest("SHA-256", encoder.encode(storedToken)),
  ]);
  const suppliedBytes = new Uint8Array(suppliedHash);
  const storedBytes = new Uint8Array(storedHash);
  let difference = 0;
  for (let index = 0; index < 32; index += 1) {
    difference |= suppliedBytes[index] ^ storedBytes[index];
  }
  return (
    match !== null &&
    tokenShapeIsValid(suppliedToken) &&
    tokenShapeIsValid(storedToken) &&
    difference === 0
  );
}

async function readJson(request, maximumBytes) {
  const contentType = request.headers.get("content-type") ?? "";
  if (!/^application\/json(?:\s*;.*)?$/i.test(contentType)) {
    throw new HttpError(415);
  }
  const declared = request.headers.get("content-length");
  if (declared !== null) {
    if (!/^\d+$/.test(declared)) throw new HttpError(400);
    if (Number(declared) > maximumBytes) throw new HttpError(413);
  }
  if (!request.body) throw new HttpError(400);

  const reader = request.body.getReader();
  const chunks = [];
  let bytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
    bytes += chunk.byteLength;
    if (bytes > maximumBytes) {
      await reader.cancel().catch(() => undefined);
      throw new HttpError(413);
    }
    chunks.push(chunk);
  }

  const combined = new Uint8Array(bytes);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(combined);
    return JSON.parse(text);
  } catch {
    throw new HttpError(400);
  }
}

async function readExactBytes(body, expectedBytes) {
  const reader = body.getReader();
  let received = 0;
  const chunks = [];
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
    received += chunk.byteLength;
    if (received > expectedBytes || received > PART_BYTES) {
      await reader.cancel().catch(() => undefined);
      throw new HttpError(received > PART_BYTES ? 413 : 400);
    }
    chunks.push(chunk);
  }
  if (received !== expectedBytes) throw new HttpError(400);
  const combined = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return combined;
}

function requireOnlyQuery(url, names) {
  const expected = new Set(names);
  const keys = [...url.searchParams.keys()];
  if (
    keys.length !== names.length ||
    keys.some((key) => !expected.has(key)) ||
    names.some((name) => url.searchParams.getAll(name).length !== 1)
  ) {
    throw new HttpError(400);
  }
  return Object.fromEntries(names.map((name) => [name, url.searchParams.get(name)]));
}

function metadataToCustom(metadata) {
  return Object.fromEntries(
    METADATA_FIELDS.map((field) => [field, String(metadata[field])]),
  );
}

function metadataFromObject(object, expectedKey) {
  if (!object || !object.customMetadata) return null;
  const custom = object.customMetadata;
  const raw = {
    format: custom.format,
    created_at: custom.created_at,
    instance_id: custom.instance_id,
    tier: custom.tier,
    database_bytes: Number(custom.database_bytes),
    database_sha256: custom.database_sha256,
    gzip_bytes: Number(custom.gzip_bytes),
    gzip_sha256: custom.gzip_sha256,
    schema_version: Number(custom.schema_version),
  };
  try {
    const validated = validateMetadata(raw);
    if (
      validated.key !== expectedKey ||
      object.key !== expectedKey ||
      object.size !== validated.metadata.gzip_bytes
    ) {
      return null;
    }
    return validated.metadata;
  } catch {
    return null;
  }
}

function metadataPayload(object, key) {
  const metadata = metadataFromObject(object, key);
  return metadata ? { key, ...metadata } : null;
}

function objectMatchesMetadata(object, key, metadata) {
  const stored = metadataFromObject(object, key);
  if (!stored) return false;
  return METADATA_FIELDS.every((field) => stored[field] === metadata[field]);
}

function safeEtag(object) {
  if (typeof object.httpEtag === "string" && /^"[^"]{1,256}"$/.test(object.httpEtag)) {
    return object.httpEtag;
  }
  if (typeof object.etag === "string" && /^[A-Za-z0-9._~:+/=-]{1,256}$/.test(object.etag)) {
    return `"${object.etag}"`;
  }
  return null;
}

function objectHeaders(object, key, metadata) {
  const headers = commonHeaders({
    "Content-Type": "application/gzip",
    "Content-Length": String(object.size),
    "Content-Disposition": `attachment; filename="${key.slice(key.lastIndexOf("/") + 1)}"`,
    "X-Backup-Format": metadata.format,
    "X-Backup-Created-At": metadata.created_at,
    "X-Backup-Instance-Id": metadata.instance_id,
    "X-Backup-Database-Bytes": String(metadata.database_bytes),
    "X-Backup-Database-SHA256": metadata.database_sha256,
    "X-Backup-Gzip-Bytes": String(metadata.gzip_bytes),
    "X-Backup-Gzip-SHA256": metadata.gzip_sha256,
    "X-Backup-Schema-Version": String(metadata.schema_version),
    "X-Backup-Key": key,
  });
  const etag = safeEtag(object);
  if (etag) headers.set("ETag", etag);
  return headers;
}

function multipartErrorStatus(error) {
  const status = Number(error?.status ?? error?.statusCode);
  if (status === 404) return 404;
  if (status === 400) return 400;
  const message = typeof error?.message === "string" ? error.message : "";
  if (/no such (?:multipart )?upload|not found|does not exist/i.test(message)) return 404;
  if (error instanceof HttpError) return error.status;
  return 500;
}

async function createUpload(request, env) {
  const { key, metadata } = validateMetadata(await readJson(request, CREATE_JSON_BYTES));
  const existing = await env.BACKUPS.head(key);
  if (existing) {
    if (!objectMatchesMetadata(existing, key, metadata)) throw new HttpError(409);
    return jsonResponse({ key, upload_id: null, existing: true });
  }

  const upload = await env.BACKUPS.createMultipartUpload(key, {
    httpMetadata: {
      contentType: "application/gzip",
      cacheControl: "no-store",
    },
    customMetadata: metadataToCustom(metadata),
  });
  if (!upload || typeof upload.uploadId !== "string" || upload.key !== key) {
    throw new HttpError(500);
  }
  return jsonResponse({ key, upload_id: upload.uploadId, existing: false }, 201);
}

async function uploadPart(request, env, url, encodedUploadId, partText) {
  const uploadId = validateUploadId(encodedUploadId);
  const partNumber = validatePartNumber(partText);
  const { key } = requireOnlyQuery(url, ["key"]);
  validateKey(key);

  const contentType = request.headers.get("content-type") ?? "";
  if (!/^application\/octet-stream(?:\s*;.*)?$/i.test(contentType)) {
    throw new HttpError(415);
  }
  const declared = request.headers.get("content-length");
  if (declared === null || !/^[1-9]\d*$/.test(declared)) throw new HttpError(400);
  const contentLength = Number(declared);
  if (!Number.isSafeInteger(contentLength)) throw new HttpError(400);
  if (contentLength > PART_BYTES) throw new HttpError(413);
  if (!request.body) throw new HttpError(400);

  const upload = env.BACKUPS.resumeMultipartUpload(key, uploadId);
  try {
    const part = await upload.uploadPart(
      partNumber,
      await readExactBytes(request.body, contentLength),
    );
    if (
      !part ||
      part.partNumber !== partNumber ||
      typeof part.etag !== "string" ||
      !ETAG_PATTERN.test(part.etag)
    ) {
      throw new HttpError(500);
    }
    return jsonResponse({ part_number: part.partNumber, etag: part.etag });
  } catch (error) {
    throw new HttpError(multipartErrorStatus(error));
  }
}

async function completeUpload(request, env, url, encodedUploadId) {
  const uploadId = validateUploadId(encodedUploadId);
  const { key } = requireOnlyQuery(url, ["key"]);
  validateKey(key);
  const parts = validateParts(await readJson(request, COMPLETE_JSON_BYTES));

  const existing = await env.BACKUPS.head(key);
  if (existing) {
    const payload = metadataPayload(existing, key);
    if (!payload) throw new HttpError(409);
    return jsonResponse(payload);
  }

  const upload = env.BACKUPS.resumeMultipartUpload(key, uploadId);
  try {
    await upload.complete(parts);
  } catch (error) {
    throw new HttpError(multipartErrorStatus(error));
  }

  const completed = await env.BACKUPS.head(key);
  const payload = metadataPayload(completed, key);
  if (!payload) throw new HttpError(409);
  return jsonResponse(payload);
}

async function abortUpload(env, url, encodedUploadId) {
  const uploadId = validateUploadId(encodedUploadId);
  const { key } = requireOnlyQuery(url, ["key"]);
  validateKey(key);
  const upload = env.BACKUPS.resumeMultipartUpload(key, uploadId);
  try {
    await upload.abort();
  } catch (error) {
    throw new HttpError(multipartErrorStatus(error));
  }
  return new Response(null, { status: 204, headers: commonHeaders() });
}

async function exactObject(request, env, url) {
  const { key } = requireOnlyQuery(url, ["key"]);
  validateKey(key);
  const object = request.method === "HEAD"
    ? await env.BACKUPS.head(key)
    : await env.BACKUPS.get(key);
  if (!object) throw new HttpError(404);
  const metadata = metadataFromObject(object, key);
  if (!metadata) throw new HttpError(409);
  const headers = objectHeaders(object, key, metadata);
  return new Response(request.method === "HEAD" ? null : object.body, {
    status: 200,
    headers,
  });
}

async function latestObject(env, url) {
  const query = requireOnlyQuery(url, ["instance", "tier"]);
  const instance = requireInstance(query.instance);
  const tier = requireTier(query.tier);
  const prefix = `backups/${instance}/${tier}/`;
  let cursor;
  let latest = null;
  const seenCursors = new Set();

  while (true) {
    const page = await env.BACKUPS.list({
      prefix,
      cursor,
      limit: 1000,
      include: ["customMetadata"],
    });
    for (const object of page.objects ?? []) {
      try {
        const parsed = validateKey(object.key);
        if (
          parsed.instance_id === instance &&
          parsed.tier === tier &&
          (!latest || object.key > latest.key)
        ) {
          latest = object;
        }
      } catch {
        // Ignore objects outside the canonical backup key format.
      }
    }
    if (!page.truncated) break;
    if (typeof page.cursor !== "string" || !page.cursor || seenCursors.has(page.cursor)) {
      throw new HttpError(500);
    }
    seenCursors.add(page.cursor);
    cursor = page.cursor;
  }

  if (!latest) throw new HttpError(404);
  const current = await env.BACKUPS.head(latest.key);
  if (!current) throw new HttpError(404);
  const payload = metadataPayload(current, latest.key);
  if (!payload) throw new HttpError(409);
  return jsonResponse(payload);
}

async function dispatchAuthenticated(request, env, url) {
  if (!env?.BACKUPS) throw new HttpError(500);
  const path = url.pathname;

  if (path === "/v1/uploads") {
    if (request.method !== "POST") return methodNotAllowed("POST");
    return createUpload(request, env);
  }

  const partRoute = /^\/v1\/uploads\/([^/]+)\/parts\/([^/]+)$/.exec(path);
  if (partRoute) {
    if (request.method !== "PUT") return methodNotAllowed("PUT");
    return uploadPart(request, env, url, partRoute[1], partRoute[2]);
  }

  const completeRoute = /^\/v1\/uploads\/([^/]+)\/complete$/.exec(path);
  if (completeRoute) {
    if (request.method !== "POST") return methodNotAllowed("POST");
    return completeUpload(request, env, url, completeRoute[1]);
  }

  const abortRoute = /^\/v1\/uploads\/([^/]+)\/abort$/.exec(path);
  if (abortRoute) {
    if (request.method !== "POST") return methodNotAllowed("POST");
    return abortUpload(env, url, abortRoute[1]);
  }

  if (path === "/v1/objects") {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return methodNotAllowed("GET, HEAD");
    }
    return exactObject(request, env, url);
  }

  if (path === "/v1/latest") {
    if (request.method !== "GET") return methodNotAllowed("GET");
    return latestObject(env, url);
  }

  return errorResponse(404);
}

async function fetch(request, env) {
  const url = new URL(request.url);
  try {
    if (url.pathname === "/healthz") {
      if (request.method !== "GET") return methodNotAllowed("GET");
      if (url.search) throw new HttpError(400);
      return jsonResponse({ status: "ok" });
    }

    if (!url.pathname.startsWith("/v1/")) return errorResponse(404);
    if (!(await authorize(request, env?.BACKUP_TOKEN))) return errorResponse(401);
    return await dispatchAuthenticated(request, env, url);
  } catch (error) {
    if (error instanceof HttpError) return errorResponse(error.status);
    return errorResponse(500);
  }
}

export default { fetch };
