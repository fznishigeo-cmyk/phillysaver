  // Optional page-memory transport. Only the current validated public snapshot survives.
  var MANIFEST_REFRESH = {{MANIFEST_SETTINGS}};
  var manifestSnapshot = null, manifestFlight = null, manifestEpoch = 0, manifestReuse = false;
{{PUBLIC_BODY}}
  function manifestAccess(meta) {
    meta = meta || state.meta || {};
    return JSON.stringify([token(), isMember(), !!meta.open_access, meta.api_url || ""]);
  }
  // A membership lookup can legitimately finish after the first public load.
  // The token and epoch own adoption; access flags separately gate reuse.
  function manifestSession() { return token(); }
  function samePublic(a, b) {
    return JSON.stringify([a.meta, a.deals, a.spotlight]) === JSON.stringify([b.meta, b.deals, b.spotlight]);
  }
  function manifestTime(value) {
    if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?(?:Z|\+00:00)$/.test(value)) return null;
    var stamp = Date.parse(value), canonical = value.replace("+00:00", "Z").replace(".000Z", "Z");
    if (!isFinite(stamp) || new Date(stamp).toISOString().replace(".000Z", "Z") !== canonical ||
        stamp > Date.now() + MANIFEST_REFRESH.max_future_s * 1000) return null;
    return stamp;
  }
  function manifestCrypto() {
    return typeof crypto !== "undefined" && crypto.subtle && typeof crypto.subtle.digest === "function" &&
      typeof TextEncoder !== "undefined" && typeof TextDecoder !== "undefined";
  }
  function publicDigest(raw) {
    if (!manifestCrypto()) return Promise.resolve(null);
    return Promise.resolve().then(function () {
      return crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw));
    }).then(function (buffer) {
      return Array.from(new Uint8Array(buffer), function (n) { return n.toString(16).padStart(2, "0"); }).join("");
    }, function () { return null; });
  }
  function readManifest(r) {
    if (!r.ok || !(r.headers.get("etag") || r.headers.get("last-modified")) ||
        !r.body || typeof r.body.getReader !== "function") throw new Error("Manifest unavailable");
    var length = r.headers.get("content-length"), limit = MANIFEST_REFRESH.max_bytes;
    var reader = r.body.getReader(), chunks = [], size = 0;
    function fail() { reader.cancel().catch(noop); throw new Error("Manifest too large"); }
    function read() {
      return reader.read().then(function (part) {
        if (!part.done) {
          size += part.value.byteLength;
          if (size > limit) return fail();
          chunks.push(part.value);
          return read();
        }
        var bytes = new Uint8Array(size), offset = 0;
        chunks.forEach(function (chunk) { bytes.set(chunk, offset); offset += chunk.byteLength; });
        return JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
      });
    }
    return Promise.resolve().then(function () {
      if (length !== null && (!/^\d+$/.test(length) || Number(length) > limit)) return fail();
      return read();
    }).finally(function () { reader.releaseLock(); });
  }
  function validManifest(m) {
    return m && typeof m === "object" && !Array.isArray(m) &&
      Object.keys(m).sort().join(",") === "bytes,published_at,schema,sha256,site_version" &&
      m.schema === 1 && typeof m.sha256 === "string" && /^[a-f0-9]{64}$/.test(m.sha256) &&
      Number.isSafeInteger(m.bytes) && m.bytes > 0 && m.bytes <= MANIFEST_REFRESH.max_deals_bytes &&
      typeof m.site_version === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(m.site_version) &&
      manifestTime(m.published_at) !== null;
  }
  function manifestCurrent(pub) {
    return !!pub && !!manifestSnapshot && pub === manifestSnapshot.pub &&
      manifestSnapshot.epoch === manifestEpoch && manifestSnapshot.session === manifestSession();
  }
  function fetchPublish() {
    if (manifestFlight) return manifestFlight;
    var epoch = manifestEpoch, access = manifestAccess(), session = manifestSession(), started = Date.now();
    function currentRequest() {
      if (epoch !== manifestEpoch || access !== manifestAccess()) throw new Error("Publish context changed");
    }
    function full() {
      currentRequest();
      return fetchPublicBody().then(function (pub) {
        var raw = pub.raw, tag = pub.tag;
        delete pub.raw; delete pub.tag;
        var stamp = manifestTime(pub.meta.published_at);
        if (stamp === null || !pub.key) throw new Error("Invalid publication time or identity");
        return publicDigest(raw).then(function (hash) {
          currentRequest();
          var previous = manifestSnapshot;
          if (previous && stamp < previous.stamp) throw new Error("Regressive publish");
          // Equal-time conflicts keep the accepted facts; hashes are never ordered.
          if (previous && stamp === previous.stamp) {
            if (hash && previous.hash ? hash !== previous.hash : !samePublic(pub, previous.pub)) {
              throw new Error("Conflicting publish");
            }
            pub.key = previous.pub.key;
            if (previous.epoch === epoch && previous.session === session) pub = previous.pub;
          } else {
            pub.key = dataKey(pub.meta) + "|" + pub.meta.published_at + "|" + (hash || "");
          }
          var bytes = typeof TextEncoder === "undefined" ? 0 : new TextEncoder().encode(raw).byteLength;
          manifestSnapshot = {pub: pub, hash: hash, bytes: bytes, stamp: stamp,
            fullAt: Date.now(), access: manifestAccess(pub.meta), epoch: epoch, session: session};
          manifestReuse = !!hash;
          clock.tag = tag || clock.tag;
          maybeReloadForNewVersion(pub.meta);
          return pub;
        });
      });
    }
    var previous = manifestSnapshot, elapsed = previous ? started - previous.fullAt : -1;
    var canCheck = manifestCrypto() && manifestReuse && previous && previous.access === access && !token() &&
      elapsed >= 0 && elapsed < MANIFEST_REFRESH.force_full_after_s * 1000;
    var check = canCheck ? fetch("publish-manifest.json", {cache: "no-cache"}).then(readManifest).then(function (m) {
      currentRequest();
      if (!validManifest(m)) throw new Error("Invalid manifest");
      return m.sha256 === previous.hash && m.bytes === previous.bytes &&
        m.published_at === previous.pub.meta.published_at && m.site_version === previous.pub.meta.site_version;
    }).catch(function () { return false; }) : Promise.resolve(false);
    var flight = check.then(function (same) {
      currentRequest();
      var age = previous ? Date.now() - previous.fullAt : -1;
      same = same && age >= 0 && age < MANIFEST_REFRESH.force_full_after_s * 1000;
      return same ? previous.pub : full();
    });
    manifestFlight = flight.finally(function () { if (manifestFlight === owned) manifestFlight = null; });
    var owned = manifestFlight;
    return owned;
  }
  function invalidateManifest() {
    manifestEpoch++;
    manifestFlight = null;
    manifestReuse = false;
    loadRequest = returnRefresh = null;
  }
  // Wall time supplies the backstop while hidden; bfcache must revalidate in full.
  window.addEventListener("pagehide", invalidateManifest);
