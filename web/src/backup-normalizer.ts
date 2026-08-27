import { JSONParser, TokenType } from '@streamparser/json';

import { summarizeBackup } from './backup';

type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

type BackupFile = {
  name: string;
  size: number;
  slice(start?: number, end?: number): { arrayBuffer(): Promise<ArrayBuffer> };
  stream(): ReadableStream<Uint8Array<ArrayBufferLike>>;
};

type StreamedBackup = {
  compressed: boolean;
  expandedBytes: number;
  format?: JsonValue;
  version?: JsonValue;
  exportedAt?: JsonValue;
  friendChunks: string[];
  friendBuffer: string[];
  eventChunks: string[];
  eventBuffer: string[];
  friendCount: number;
  eventCount: number;
  rawFetchCount: number;
  hasFriendsArray: boolean;
  hasEventsArray: boolean;
};

const NORMALIZED_CHUNK_RECORDS = 512;
const QUOTE = 0x22;
const BACKSLASH = 0x5c;
const LEFT_BRACE = 0x7b;
const RIGHT_BRACE = 0x7d;
const LEFT_BRACKET = 0x5b;
const RIGHT_BRACKET = 0x5d;
const COLON = 0x3a;
const COMMA = 0x2c;

const isWhitespace = (byte: number) =>
  byte === 0x20 || byte === 0x09 || byte === 0x0a || byte === 0x0d;

/**
 * Removes the intentionally unsupported raw response array before the JSON
 * library sees it.  Raw bodies can contain very large Base64 strings; feeding
 * those strings to a normal SAX parser still materializes each complete value.
 * This scanner only retains a bounded top-level key and balances the skipped
 * array, so memory use is independent of an individual raw response body.
 */
class RawFetchRedactor {
  private depth = 0;
  private inString = false;
  private escaped = false;
  private captureKey = false;
  private keyBytes: number[] = [];
  private expectingTopLevelKey = false;
  private awaitingTopLevelValue = false;
  private topLevelKey: string | undefined;
  private readonly topLevelKeys = new Set<string>();
  private skippingRaw = false;
  private rawStack: number[] = [];
  private rawInString = false;
  private rawEscaped = false;
  private rawExpectElement = true;
  private rawSawElement = false;
  rawFetchCount = 0;

  private finishTopLevelKey() {
    let key: unknown;
    try {
      const encoded = new Uint8Array(this.keyBytes);
      key = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(encoded));
    } catch {
      throw new Error('invalid top-level key');
    }
    if (typeof key !== 'string' || this.topLevelKeys.has(key)) {
      throw new Error('duplicate or invalid top-level key');
    }
    this.topLevelKeys.add(key);
    this.topLevelKey = key;
    this.expectingTopLevelKey = false;
    this.captureKey = false;
    this.keyBytes = [];
  }

  private processSkippedRawByte(byte: number): boolean {
    if (this.rawInString) {
      if (this.rawEscaped) this.rawEscaped = false;
      else if (byte === BACKSLASH) this.rawEscaped = true;
      else if (byte === QUOTE) this.rawInString = false;
      else if (byte < 0x20) throw new Error('invalid control byte in raw response');
      return false;
    }
    if (isWhitespace(byte)) return false;

    const atArrayRoot = this.rawStack.length === 1;
    if (atArrayRoot && byte === RIGHT_BRACKET) {
      if (this.rawExpectElement && this.rawSawElement) {
        throw new Error('trailing comma in raw response array');
      }
      this.rawStack.pop();
      this.skippingRaw = false;
      return true;
    }
    if (atArrayRoot && byte === COMMA) {
      if (this.rawExpectElement) throw new Error('invalid raw response array');
      this.rawExpectElement = true;
      return false;
    }
    if (atArrayRoot && this.rawExpectElement) {
      this.rawFetchCount += 1;
      this.rawSawElement = true;
      this.rawExpectElement = false;
    }

    if (byte === QUOTE) {
      this.rawInString = true;
      this.rawEscaped = false;
    } else if (byte === LEFT_BRACE || byte === LEFT_BRACKET) {
      this.rawStack.push(byte);
    } else if (byte === RIGHT_BRACE || byte === RIGHT_BRACKET) {
      const expected = byte === RIGHT_BRACE ? LEFT_BRACE : LEFT_BRACKET;
      if (this.rawStack.at(-1) !== expected) throw new Error('unbalanced raw response');
      this.rawStack.pop();
    }
    return false;
  }

  write(input: Uint8Array): Uint8Array[] {
    const output: Uint8Array[] = [];
    let sliceStart = 0;
    const emitThrough = (end: number) => {
      if (end > sliceStart) output.push(input.subarray(sliceStart, end));
      sliceStart = end;
    };

    for (let index = 0; index < input.byteLength; index += 1) {
      const byte = input[index]!;
      if (this.skippingRaw) {
        if (this.processSkippedRawByte(byte)) sliceStart = index + 1;
        continue;
      }

      if (this.inString) {
        if (this.captureKey) {
          this.keyBytes.push(byte);
          if (this.keyBytes.length > 1024) throw new Error('top-level key too large');
        }
        if (this.escaped) this.escaped = false;
        else if (byte === BACKSLASH) this.escaped = true;
        else if (byte === QUOTE) {
          this.inString = false;
          if (this.captureKey) this.finishTopLevelKey();
        }
        continue;
      }

      if (this.awaitingTopLevelValue && this.depth === 1) {
        if (isWhitespace(byte)) continue;
        this.awaitingTopLevelValue = false;
        if (this.topLevelKey === 'raw_fetches') {
          if (byte !== LEFT_BRACKET) throw new Error('raw_fetches must be an array');
          emitThrough(index + 1);
          output.push(new Uint8Array([RIGHT_BRACKET]));
          this.skippingRaw = true;
          this.rawStack = [LEFT_BRACKET];
          this.rawInString = false;
          this.rawEscaped = false;
          this.rawExpectElement = true;
          this.rawSawElement = false;
          sliceStart = index + 1;
          continue;
        }
      }

      if (byte === QUOTE) {
        this.inString = true;
        this.escaped = false;
        if (this.depth === 1 && this.expectingTopLevelKey) {
          this.captureKey = true;
          this.keyBytes = [QUOTE];
        }
      } else if (byte === LEFT_BRACE) {
        this.depth += 1;
        if (this.depth === 1) this.expectingTopLevelKey = true;
      } else if (byte === LEFT_BRACKET) {
        this.depth += 1;
      } else if (byte === RIGHT_BRACE || byte === RIGHT_BRACKET) {
        this.depth -= 1;
      } else if (byte === COLON && this.depth === 1 && this.topLevelKey !== undefined) {
        this.awaitingTopLevelValue = true;
      } else if (byte === COMMA && this.depth === 1) {
        this.topLevelKey = undefined;
        this.expectingTopLevelKey = true;
      }
    }
    if (!this.skippingRaw) emitThrough(input.byteLength);
    return output;
  }

  end() {
    if (this.skippingRaw || this.rawInString) throw new Error('unfinished raw response array');
  }
}

function appendRecord(chunks: string[], buffer: string[], encoded: string, count: number) {
  buffer.push(count ? `,${encoded}` : encoded);
  if (buffer.length >= NORMALIZED_CHUNK_RECORDS) {
    chunks.push(buffer.join(''));
    buffer.length = 0;
  }
}

export type NormalizedBackupResult =
  | {
      ok: true;
      preview: {
        format: string;
        exportedAt: string;
        friends: number;
        events: number;
        rawFetches: number;
      };
      upload: File;
    }
  | {
      ok: false;
      reason?: 'input-too-large' | 'expanded-too-large' | 'normalized-too-large' | 'invalid';
    };

async function parseBackup(file: BackupFile, maximumExpanded: number): Promise<StreamedBackup> {
  const signature = new Uint8Array(await file.slice(0, 2).arrayBuffer());
  const compressed = signature[0] === 0x1f && signature[1] === 0x8b;
  let stream = file.stream() as ReadableStream<Uint8Array<ArrayBufferLike>>;
  if (compressed) {
    if (typeof DecompressionStream !== 'function') throw new Error('gzip unsupported');
    stream = (
      file.stream() as unknown as ReadableStream<BufferSource>
    ).pipeThrough(new DecompressionStream('gzip')) as ReadableStream<Uint8Array<ArrayBufferLike>>;
  }

  const result: StreamedBackup = {
    compressed,
    expandedBytes: 0,
    friendChunks: [],
    friendBuffer: [],
    eventChunks: [],
    eventBuffer: [],
    friendCount: 0,
    eventCount: 0,
    rawFetchCount: 0,
    hasFriendsArray: false,
    hasEventsArray: false,
  };
  let invalidItem = false;
  let rootIsObject = false;
  let topLevelKey: string | undefined;
  type ContainerState =
    | { kind: 'object'; keys: Set<string>; expectingKey: boolean }
    | { kind: 'array' };
  const containers: ContainerState[] = [];
  const redactor = new RawFetchRedactor();
  const parser = new JSONParser({
    paths: [
      '$.format',
      '$.version',
      '$.exported_at',
      '$.friends.*',
      '$.status_events.*',
    ],
    keepStack: false,
    stringBufferSize: 64 * 1024,
  });
  parser.onToken = ({ token, value }) => {
    if (token === TokenType.LEFT_BRACE) {
      if (containers.length === 0) rootIsObject = true;
      containers.push({ kind: 'object', keys: new Set<string>(), expectingKey: true });
    } else if (token === TokenType.LEFT_BRACKET) {
      if (containers.length === 1 && topLevelKey === 'friends') result.hasFriendsArray = true;
      if (containers.length === 1 && topLevelKey === 'status_events') result.hasEventsArray = true;
      containers.push({ kind: 'array' });
    } else if (token === TokenType.RIGHT_BRACE || token === TokenType.RIGHT_BRACKET) {
      containers.pop();
    } else if (token === TokenType.STRING) {
      const current = containers.at(-1);
      if (current?.kind === 'object' && current.expectingKey) {
        const key = String(value);
        if (current.keys.has(key)) invalidItem = true;
        current.keys.add(key);
        current.expectingKey = false;
        if (containers.length === 1) topLevelKey = key;
      }
    } else if (token === TokenType.COMMA) {
      const current = containers.at(-1);
      if (current?.kind === 'object') {
        current.expectingKey = true;
        if (containers.length === 1) topLevelKey = undefined;
      }
    }
  };
  parser.onValue = ({ value, key, stack }) => {
    const parentKey = stack.at(-1)?.key;
    if (stack.length === 1 && key === 'format') result.format = value as JsonValue;
    else if (stack.length === 1 && key === 'version') result.version = value as JsonValue;
    else if (stack.length === 1 && key === 'exported_at') result.exportedAt = value as JsonValue;
    else if (parentKey === 'friends') {
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        invalidItem = true;
        return;
      }
      const encoded = JSON.stringify(value);
      appendRecord(result.friendChunks, result.friendBuffer, encoded, result.friendCount);
      result.friendCount += 1;
    } else if (parentKey === 'status_events') {
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        invalidItem = true;
        return;
      }
      const encoded = JSON.stringify(value);
      appendRecord(result.eventChunks, result.eventBuffer, encoded, result.eventCount);
      result.eventCount += 1;
    }
  };

  const reader = stream.getReader();
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximumExpanded) throw new Error('expanded backup too large');
      for (const chunk of redactor.write(value)) parser.write(chunk);
    }
    redactor.end();
    if (!parser.isEnded) parser.end();
  } finally {
    reader.releaseLock();
  }
  if (!rootIsObject || invalidItem || !result.hasFriendsArray || !result.hasEventsArray) {
    throw new Error('invalid backup shape');
  }
  result.rawFetchCount = redactor.rawFetchCount;
  result.expandedBytes = total;
  return result;
}

export async function normalizeBackupFile(
  file: BackupFile,
  maximum: number,
  maximumSourceExpanded: number,
  maximumServerExpanded: number = maximumSourceExpanded,
): Promise<NormalizedBackupResult> {
  if (
    !Number.isFinite(maximum) ||
    maximum <= 0 ||
    !Number.isFinite(maximumSourceExpanded) ||
    maximumSourceExpanded <= 0 ||
    !Number.isFinite(maximumServerExpanded) ||
    maximumServerExpanded < maximum ||
    maximumServerExpanded > maximumSourceExpanded ||
    file.size > maximumSourceExpanded
  ) {
    return { ok: false, reason: 'input-too-large' };
  }
  try {
    const streamed = await parseBackup(file, maximumSourceExpanded);
    const summary = summarizeBackup({
      format: streamed.format,
      version: streamed.version,
      exported_at: streamed.exportedAt,
      friends: [],
      status_events: [],
    });
    if (!summary.ok) return { ...summary, reason: 'invalid' };
    const normalized = new File(
      [
        '{"format":',
        JSON.stringify(streamed.format),
        ',"version":',
        JSON.stringify(streamed.version),
        ',"exported_at":',
        JSON.stringify(streamed.exportedAt ?? ''),
        ',"friends":[',
        ...streamed.friendChunks,
        streamed.friendBuffer.join(''),
        '],"status_events":[',
        ...streamed.eventChunks,
        streamed.eventBuffer.join(''),
        ']}',
      ],
      `${file.name.replace(/(?:\.json)?\.gz$/i, '').replace(/\.json$/i, '')}.normalized.json`,
      { type: 'application/json' },
    );
    let upload = normalized;
    if (normalized.size > maximum) {
      if (
        streamed.compressed
        && streamed.rawFetchCount === 0
        && streamed.expandedBytes <= maximumServerExpanded
        && file.size <= maximum
        && file instanceof Blob
      ) {
        upload = new File([file], file.name, { type: 'application/gzip' });
      } else {
        return { ok: false, reason: 'normalized-too-large' };
      }
    }
    return {
      ok: true,
      preview: {
        ...summary.preview,
        friends: streamed.friendCount,
        events: streamed.eventCount,
        rawFetches: streamed.rawFetchCount,
      },
      upload,
    };
  } catch (error) {
    const reason = error instanceof Error && error.message === 'expanded backup too large'
      ? 'expanded-too-large'
      : 'invalid';
    return { ok: false, reason };
  }
}
