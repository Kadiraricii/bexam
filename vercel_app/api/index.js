// Vercel Serverless Function: Tekil API Router (/api/sync & /api/view)
// Upstash Redis ile kalıcı depolama — tüm instance'lar aynı veriye erişir
const { Redis } = require('@upstash/redis');

const redis = new Redis({
  url: process.env.UPSTASH_REDIS_REST_URL,
  token: process.env.UPSTASH_REDIS_REST_TOKEN,
});

const MAX_ATTEMPTS = 5;
const LOCKOUT_MS = 5 * 60 * 1000; // 5 dakika
const TTL_SECONDS = 24 * 60 * 60;  // 24 saat TTL

async function getAttemptState(refCode) {
  const val = await redis.get(`attempts:${refCode}`);
  return val || { count: 0, lockedUntil: 0 };
}

async function setAttemptState(refCode, state) {
  await redis.set(`attempts:${refCode}`, state, { ex: TTL_SECONDS });
}

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  const urlPath = (req.url || "").split("?")[0].toLowerCase();
  const isSync = urlPath.endsWith("/sync") || req.query.action === "sync";

  // ============================
  // POST /api/sync — veri yükle
  // ============================
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
        await redis.del(`session:${key}`);
        return res.status(200).json({ success: true, deleted: true, ref_code: key });
      }

      if (!body.pin || !body.data) {
        return res.status(400).json({ error: "Eksik parametreler (pin, data)" });
      }

      await redis.set(`session:${key}`, {
        pin: String(body.pin).trim(),
        timestamp: body.timestamp || Date.now() / 1000,
        data: body.data,
      }, { ex: TTL_SECONDS });

      return res.status(200).json({ success: true, ref_code: key, updated_at: Date.now() });
    } catch (err) {
      return res.status(500).json({ error: err.message });
    }
  }

  // ============================
  // GET/POST /api/view — veri oku
  // ============================
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

  const state = await getAttemptState(ref_code);
  const now = Date.now();

  if (state.lockedUntil > now) {
    const waitSeconds = Math.ceil((state.lockedUntil - now) / 1000);
    return res.status(429).json({
      error: `Çok fazla hatalı deneme. Lütfen ${waitSeconds} saniye sonra tekrar deneyin.`,
      locked: true,
      retry_after_seconds: waitSeconds,
    });
  }

  const record = await redis.get(`session:${ref_code}`);

  if (!record) {
    return res.status(404).json({
      error: `Bu Referans Kodu (${ref_code}) ile henüz canlı yayın başlatılmamış. Masaüstü uygulamasındaki Ayarlar menüsünden "Bulut Yayını Başlat" butonuna tıkladığından emin ol.`
    });
  }

  if (record.pin !== pin) {
    const nextCount = state.count + 1;
    const remaining = Math.max(MAX_ATTEMPTS - nextCount, 0);
    if (nextCount >= MAX_ATTEMPTS) {
      await setAttemptState(ref_code, { count: nextCount, lockedUntil: now + LOCKOUT_MS });
      return res.status(429).json({
        error: `Çok fazla hatalı deneme. ${Math.ceil(LOCKOUT_MS / 60000)} dakika boyunca bu kod için giriş kilitlendi.`,
        locked: true,
        retry_after_seconds: Math.ceil(LOCKOUT_MS / 1000),
      });
    }
    await setAttemptState(ref_code, { count: nextCount, lockedUntil: 0 });
    return res.status(401).json({
      error: `Hatalı PIN Kodu! Lütfen masaüstü uygulamasındaki 4 haneli PIN kodunu kontrol edin.`,
      attempts_remaining: remaining,
    });
  }

  // Başarılı giriş - deneme sayacını sıfırla
  await redis.del(`attempts:${ref_code}`);

  return res.status(200).json({
    success: true,
    ref_code: ref_code,
    timestamp: record.timestamp,
    data: record.data
  });
};
