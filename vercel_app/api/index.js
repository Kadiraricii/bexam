// Vercel Serverless Function: Tekil API Router (/api/sync & /api/view)
const fs = require('fs');
const path = require('path');

const TMP_FILE = path.join('/tmp', 'bb_cloud_store.json');
const store = global._bb_cloud_store || (global._bb_cloud_store = new Map());
const attempts = global._bb_login_attempts || (global._bb_login_attempts = new Map());

const MAX_ATTEMPTS = 5;
const LOCKOUT_MS = 5 * 60 * 1000; // 5 dakika

function loadPersistedStore() {
  if (fs.existsSync(TMP_FILE)) {
    try {
      const raw = fs.readFileSync(TMP_FILE, 'utf8');
      const obj = JSON.parse(raw);
      Object.keys(obj).forEach(k => {
        store.set(k, obj[k]);
      });
    } catch (e) {}
  }
}

function savePersistedStore() {
  try {
    const obj = {};
    store.forEach((val, key) => {
      obj[key] = val;
    });
    fs.writeFileSync(TMP_FILE, JSON.stringify(obj), 'utf8');
  } catch (e) {}
}

// Cold-start aninda diskten hafizaya yukle
if (store.size === 0) {
  loadPersistedStore();
}

function getAttemptState(key) {
  return attempts.get(key) || { count: 0, lockedUntil: 0 };
}

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  // URL yolunu tespit et (ör: /api/sync vs /api/view)
  const urlPath = (req.url || "").split("?")[0].toLowerCase();
  const isSync = urlPath.endsWith("/sync") || req.query.action === "sync";

  if (isSync) {
    if (req.method !== "POST") {
      return res.status(405).json({ error: "Method not allowed for /api/sync" });
    }

    try {
      let body = req.body;
      if (typeof body === "string") {
        try { body = JSON.parse(body); } catch (e) {}
      }

      if (!body || !body.ref_code) {
        return res.status(400).json({ error: "Eksik parametreler (ref_code)" });
      }

      const key = String(body.ref_code).trim().toUpperCase();

      if (body.action === "delete") {
        store.delete(key);
        savePersistedStore();
        return res.status(200).json({ success: true, deleted: true, ref_code: key });
      }

      if (!body.pin || !body.data) {
        return res.status(400).json({ error: "Eksik parametreler (pin, data)" });
      }

      store.set(key, {
        pin: String(body.pin).trim(),
        timestamp: body.timestamp || Date.now() / 1000,
        data: body.data
      });
      savePersistedStore();

      return res.status(200).json({ success: true, ref_code: key, updated_at: Date.now() });
    } catch (err) {
      return res.status(500).json({ error: err.message });
    }
  }

  // ---------- /api/view ----------
  let ref_code = "";
  let pin = "";

  if (req.method === "POST") {
    let body = req.body;
    if (typeof body === "string") {
      try { body = JSON.parse(body); } catch (e) {}
    }
    ref_code = String((body && body.ref_code) || req.query.ref_code || "").trim().toUpperCase();
    pin = String((body && body.pin) || req.query.pin || "").trim();
  } else {
    ref_code = String(req.query.ref_code || "").trim().toUpperCase();
    pin = String(req.query.pin || "").trim();
  }

  if (!ref_code || !pin) {
    return res.status(400).json({ error: "Referans Kodu ve PIN Kodu gereklidir." });
  }

  const state = getAttemptState(ref_code);
  const now = Date.now();

  if (state.lockedUntil > now) {
    const waitSeconds = Math.ceil((state.lockedUntil - now) / 1000);
    return res.status(429).json({
      error: `Çok fazla hatalı deneme. Lütfen ${waitSeconds} saniye sonra tekrar deneyin.`,
      locked: true,
      retry_after_seconds: waitSeconds,
    });
  }

  // Eger hafiza bos kalmissa tekrar diskten kontrol et
  if (!store.has(ref_code)) {
    loadPersistedStore();
  }

  const record = store.get(ref_code);
  if (!record) {
    return res.status(404).json({
      error: `Bu Referans Kodu (${ref_code}) ile henüz canlı yayın başlatılmamış. Masaüstü uygulamasındaki Ayarlar menüsünden "Bulut Yayını Başlat" butonuna tıkladığından emin ol.`
    });
  }

  if (record.pin !== pin) {
    const nextCount = state.count + 1;
    const remaining = Math.max(MAX_ATTEMPTS - nextCount, 0);
    if (nextCount >= MAX_ATTEMPTS) {
      attempts.set(ref_code, { count: nextCount, lockedUntil: now + LOCKOUT_MS });
      return res.status(429).json({
        error: `Çok fazla hatalı deneme. ${Math.ceil(LOCKOUT_MS / 60000)} dakika boyunca bu kod için giriş kilitlendi.`,
        locked: true,
        retry_after_seconds: Math.ceil(LOCKOUT_MS / 1000),
      });
    }
    attempts.set(ref_code, { count: nextCount, lockedUntil: 0 });
    return res.status(401).json({
      error: `Hatalı PIN Kodu! Lütfen masaüstü uygulamasındaki 4 haneli PIN kodunu kontrol edin.`,
      attempts_remaining: remaining,
    });
  }

  // Basarili giris
  attempts.delete(ref_code);

  return res.status(200).json({
    success: true,
    ref_code: ref_code,
    timestamp: record.timestamp,
    data: record.data
  });
};
