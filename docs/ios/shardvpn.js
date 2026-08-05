// shardVPN control client for Scriptable (https://scriptable.app).
//
// First run prompts for the Function URL and signing secret and stores both
// in the iOS keychain. Subsequent runs sign and send a request.
//
// Run with a parameter of "up", "down" or "status" from a Shortcuts wrapper,
// or with no parameter to be prompted.

const KEY_URL = "shardvpn.url";
const KEY_SECRET = "shardvpn.secret";
const MAX_ATTEMPTS = 4;

// --- UTF-8 encoding (TextEncoder is not guaranteed in JavaScriptCore) ------

function utf8Bytes(str) {
  const out = [];
  for (const ch of str) {
    let code = ch.codePointAt(0);
    if (code < 0x80) out.push(code);
    else if (code < 0x800) out.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
    else if (code < 0x10000)
      out.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
    else
      out.push(
        0xf0 | (code >> 18),
        0x80 | ((code >> 12) & 0x3f),
        0x80 | ((code >> 6) & 0x3f),
        0x80 | (code & 0x3f)
      );
  }
  return out;
}

// --- vendored HMAC-SHA256 (no dependencies, no network) -------------------

function sha256(bytes) {
  const K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  const h = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ];

  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  const len = bytes.length;
  const padded = bytes.slice();
  padded.push(0x80);
  while (padded.length % 64 !== 56) padded.push(0);
  const bits = len * 8;
  for (let i = 7; i >= 0; i--) padded.push(Math.floor(bits / Math.pow(2, i * 8)) & 0xff);

  for (let chunk = 0; chunk < padded.length; chunk += 64) {
    const w = new Array(64);
    for (let i = 0; i < 16; i++) {
      const j = chunk + i * 4;
      w[i] = (padded[j] << 24) | (padded[j + 1] << 16) | (padded[j + 2] << 8) | padded[j + 3];
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let [a, b, c, d, e, f, g, hh] = h;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + K[i] + w[i]) | 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      hh = g; g = f; f = e; e = (d + t1) | 0;
      d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    const next = [a, b, c, d, e, f, g, hh];
    for (let i = 0; i < 8; i++) h[i] = (h[i] + next[i]) | 0;
  }

  const out = [];
  for (const v of h) out.push((v >>> 24) & 0xff, (v >>> 16) & 0xff, (v >>> 8) & 0xff, v & 0xff);
  return out;
}

function hmacSha256Hex(keyStr, messageStr) {
  let key = utf8Bytes(keyStr);
  if (key.length > 64) key = sha256(key);
  while (key.length < 64) key.push(0);

  const inner = key.map((b) => b ^ 0x36).concat(utf8Bytes(messageStr));
  const outer = key.map((b) => b ^ 0x5c).concat(sha256(inner));
  return sha256(outer).map((b) => b.toString(16).padStart(2, "0")).join("");
}

// --- configuration --------------------------------------------------------

async function prompt(title, message) {
  const alert = new Alert();
  alert.title = title;
  alert.message = message;
  alert.addTextField("value");
  alert.addAction("Save");
  await alert.present();
  return alert.textFieldValue(0).trim();
}

// Named loadConfig, not config: `config` is a Scriptable global.
async function loadConfig() {
  if (!Keychain.contains(KEY_URL)) {
    Keychain.set(KEY_URL, await prompt("shardVPN", "Lambda Function URL"));
  }
  if (!Keychain.contains(KEY_SECRET)) {
    Keychain.set(KEY_SECRET, await prompt("shardVPN", "Signing secret"));
  }
  return { url: Keychain.get(KEY_URL), secret: Keychain.get(KEY_SECRET) };
}

// --- request --------------------------------------------------------------

async function send(url, secret, action, region) {
  // Reserved concurrency is 1, so a concurrent watchdog sweep returns 429.
  // Retry rather than reporting a transient lockout as a failure.
  let lastError = null;

  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    // Region is omitted entirely unless asked for, so the server falls back
    // to /shardvpn/default-region. An unknown region comes back as a 400
    // listing the valid ones, so a typo is legible rather than mysterious.
    const body = JSON.stringify(region ? { action, region } : { action });
    const timestamp = Math.floor(Date.now() / 1000).toString();

    const request = new Request(url);
    request.method = "POST";
    request.headers = {
      "Content-Type": "application/json",
      "X-ShardVPN-Timestamp": timestamp,
      "X-ShardVPN-Signature": hmacSha256Hex(secret, `${timestamp}.${body}`),
    };
    request.body = body;

    try {
      const result = await request.loadJSON();
      const code = request.response.statusCode;
      if (code === 429 || code >= 500) {
        lastError = `http ${code}`;
      } else if (code >= 400) {
        return { error: result.error || `http ${code}` };
      } else {
        return result;
      }
    } catch (e) {
      lastError = String(e);
    }

    if (attempt < MAX_ATTEMPTS - 1) {
      // 1s, 2s, 4s. Re-signs each attempt so the timestamp stays fresh. Only
      // sleeps between attempts — sleeping after the last one would make a
      // request that fails all four tries wait an extra 8s before showing
      // an error the user could have seen immediately.
      await new Promise((resolve) => Timer.schedule(1000 * Math.pow(2, attempt), false, resolve));
    }
  }

  return { error: lastError || "unreachable" };
}

// --- notification text -----------------------------------------------------

// This is read on a lock screen, one line, usually while walking somewhere.
// Say what happened and what to do about it — nothing else.
function describe(result) {
  if (result.error) return `error: ${result.error}`;

  // No node: the tailnet field is necessarily "unknown", so printing it is
  // noise. The first version rendered this as the baffling "absent unknown".
  if (result.state === "absent") return "no node running";

  const where = result.region || "";

  // Booted but not on the tailnet. Distinguishing this from "ready" is the
  // whole reason status queries Tailscale as well as EC2 — otherwise a node
  // that silently failed to join looks identical to one still starting.
  if (result.tailnet === "absent") {
    return `${where}: starting, not on tailnet yet (${result.age || "0m"})`;
  }

  if (result.tailnet === "online") {
    const idle = result.idle_for ? `, idle ${result.idle_for}` : "";
    return `${where}: ready${idle}`;
  }

  return `${where}: ${result.state}`;
}

// --- main -----------------------------------------------------------------

async function main() {
  const { url, secret } = await loadConfig();

  // A shortcut parameter may carry a region: "up:eu-west-1". Plain "up",
  // "down" and "status" still work, so existing Shortcuts are unaffected —
  // and a per-region Shortcut is just one with "up:<region>" as its text.
  const raw = args.shortcutParameter || args.queryParameters?.action;
  let action = raw;
  let region = null;

  if (raw && raw.includes(":")) {
    const [a, r] = raw.split(":", 2);
    action = a;
    region = r;
  }

  if (!action) {
    const menu = new Alert();
    menu.title = "shardVPN";
    // "up" uses the default region from SSM, which is the common case and
    // stays one tap. "up elsewhere…" exists because the whole point of the
    // region being a request parameter is launching near wherever you are —
    // and changing the SSM default from a phone is not realistic.
    const choices = ["status", "up", "up elsewhere…", "down"];
    choices.forEach((a) => menu.addAction(a));
    menu.addCancelAction("cancel");
    const chosen = await menu.presentSheet();
    if (chosen < 0) return;

    if (choices[chosen] === "up elsewhere…") {
      action = "up";
      const ask = new Alert();
      ask.title = "Region";
      ask.message = "AWS region code, e.g. eu-west-1";
      ask.addTextField("region");
      ask.addAction("Launch");
      ask.addCancelAction("cancel");
      if ((await ask.present()) < 0) return;
      region = ask.textFieldValue(0).trim();
      if (!region) return;
    } else {
      action = choices[chosen];
    }
  }

  const result = await send(url, secret, action, region);

  const notification = new Notification();
  notification.title = "shardVPN";
  notification.body = describe(result);
  await notification.schedule();

  console.log(JSON.stringify(result, null, 2));
  Script.complete();
}

await main();
