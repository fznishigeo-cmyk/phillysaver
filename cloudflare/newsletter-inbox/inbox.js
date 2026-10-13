// Private, seven-day email staging. Nothing here sends new mail or logs a body.
export const TTL_SECONDS = 7 * 24 * 60 * 60;
const encoder = new TextEncoder();
const ID_RE = /^msg:[a-f0-9]{64}$/;
const ACK_MAX_BYTES = 16384;
const ACK_MAX_IDS = 100;

function limit(env, name, fallback) {
  const n = Number(env[name] || fallback);
  if (!Number.isSafeInteger(n) || n < 1) throw new Error("invalid inbox size limit");
  return n;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
  } });
}

async function digest(value) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", value));
}

// Hash both inputs to a fixed size before comparing every byte. No early exit
// for a token's length or matching prefix, including wrong-length candidates.
export async function authorized(request, env) {
  if (!env.NEWSLETTER_INBOX_TOKEN) return false;
  const supplied = request.headers.get("Authorization") || "";
  if (supplied.length > 4096) return false;
  const [a, b] = await Promise.all([
    digest(encoder.encode(supplied)),
    digest(encoder.encode(`Bearer ${env.NEWSLETTER_INBOX_TOKEN}`)),
  ]);
  let difference = 0;
  for (let i = 0; i < 32; i++) difference |= a[i] ^ b[i];
  return difference === 0;
}

async function readBounded(stream, maximum) {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximum) {
        await reader.cancel();
        throw new RangeError("inbox payload too large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}

function header(value, maximum) { return String(value || "").slice(0, maximum); }

export function createInboxWorker(parseMime) {
  return {
    async email(message, env) {
      if (!env.NEWSLETTER_FORWARD_TO) throw new Error("inbox forwarding destination missing");
      // Forward the original message, even if too large/malformed for staging.
      try { await message.forward(env.NEWSLETTER_FORWARD_TO); }
      catch { throw new Error("newsletter inbox forwarding failed"); }
      const rawLimit = limit(env, "MAX_RAW_BYTES", 1048576);
      if (message.rawSize > rawLimit) return;
      let raw, parsed;
      try {
        raw = await readBounded(message.raw, rawLimit);
        parsed = await parseMime(raw);
      } catch {
        console.warn("newsletter inbox: oversized or unreadable email; forwarded only");
        return;
      }
      const hash = await digest(raw);
      const id = "msg:" + [...hash].map((b) => b.toString(16).padStart(2, "0")).join("");
      const receivedAt = new Date().toISOString();
      const record = {
        id,
        from: header(parsed.from?.address || message.from, 320).trim().toLowerCase(),
        envelope_from: header(message.from, 320),
        subject: header(parsed.subject || message.headers.get("subject"), 2048),
        date: header(parsed.date || message.headers.get("date"), 512),
        message_id: header(parsed.messageId || message.headers.get("message-id"), 512),
        received_at: receivedAt,
        text: String(parsed.text || ""), html: String(parsed.html || ""),
      };
      const value = JSON.stringify(record);
      if (encoder.encode(value).byteLength > limit(env, "MAX_MESSAGE_BYTES", 262144)) return;
      try {
        await env.INBOX.put(id, value, {
          expirationTtl: TTL_SECONDS, metadata: { received_at: receivedAt },
        });
      } catch { throw new Error("newsletter inbox storage failed"); }
    },

    async fetch(request, env) {
      if (!await authorized(request, env)) return json({ error: "unauthorized" }, 401);
      const url = new URL(request.url);
      try {
        if (url.pathname === "/inbox") {
          if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
          const since = url.searchParams.get("since");
          if (since && (!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(since)
                        || !Number.isFinite(Date.parse(since)))) {
            return json({ error: "invalid_since" }, 400);
          }
          const cursor = url.searchParams.get("cursor") || undefined;
          if (cursor && cursor.length > 1024) return json({ error: "invalid_cursor" }, 400);
          const responseLimit = limit(env, "MAX_RESPONSE_BYTES", 2097152);
          const messageLimit = limit(env, "MAX_MESSAGE_BYTES", 262144);
          // Every listed record fits; never advance a cursor past an omitted
          // message just because the response was full.
          const sizeLimit = Math.floor((responseLimit - 2048) / (messageLimit + 1));
          if (sizeLimit < 1) throw new Error("inbox response limit too small");
          const requested = Number(url.searchParams.get("limit") || 25);
          if (!Number.isSafeInteger(requested) || requested < 1) return json({ error: "invalid_limit" }, 400);
          const page = await env.INBOX.list({ prefix: "msg:", cursor,
            limit: Math.min(100, requested, sizeLimit, limit(env, "PAGE_SIZE", 25)) });
          const cutoff = Math.max(Date.now() - TTL_SECONDS * 1000, since ? Date.parse(since) : 0);
          const messages = [];
          for (const key of page.keys) {
            if (Date.parse(key.metadata?.received_at || "") < cutoff) continue;
            const value = await env.INBOX.get(key.name);
            if (!value || encoder.encode(value).byteLength > messageLimit) continue;
            const record = JSON.parse(value);
            const received = Date.parse(record.received_at || "");
            if (Number.isFinite(received) && received >= cutoff && record.id === key.name) messages.push(record);
          }
          const result = { messages, next_cursor: page.list_complete ? null : page.cursor };
          if (encoder.encode(JSON.stringify(result)).byteLength > responseLimit) throw new Error("inbox response too large");
          return json(result);
        }
        if (url.pathname === "/inbox/ack") {
          if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
          const declared = Number(request.headers.get("Content-Length") || 0);
          if (declared > ACK_MAX_BYTES) return json({ error: "too_large" }, 413);
          let payload;
          try {
            const raw = await readBounded(request.body, ACK_MAX_BYTES);
            payload = JSON.parse(new TextDecoder().decode(raw));
          } catch (error) {
            return json({ error: error instanceof RangeError ? "too_large" : "invalid_json" },
                        error instanceof RangeError ? 413 : 400);
          }
          const ids = payload?.ids;
          if (!Array.isArray(ids) || ids.length > ACK_MAX_IDS ||
              ids.some((id) => typeof id !== "string" || !ID_RE.test(id))) {
            return json({ error: "invalid_ids" }, 400);
          }
          for (const id of new Set(ids)) await env.INBOX.delete(id);
          return json({ acknowledged: ids.length });
        }
        return json({ error: "not_found" }, 404);
      } catch {
        return json({ error: "inbox_unavailable" }, 503);
      }
    },
  };
}
