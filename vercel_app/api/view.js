// Vercel Serverless Function: Referans Kodu & PIN ile Durum Sorgulama (POST/GET /api/view)
const store = global._bb_cloud_store || (global._bb_cloud_store = new Map());

// Basarisiz giris denemelerini ref_code basina sayan, sunucu bellekte
// tutulan bir kilitleme mekanizmasi. Onceden PIN girisinde HICBIR deneme
// siniri yoktu - 4 haneli bir PIN sadece 10.000 olasilik tasidigi icin
// bu, otomatik bir script ile saniyeler icinde kaba-kuvvetle (brute force)
// kirilabilirdi. MAX_ATTEMPTS basarisiz denemeden sonra LOCKOUT_MS suresince
// o ref_code icin TUM giris denemeleri (dogru PIN girilse bile) reddedilir.
const attempts = global._bb_login_attempts || (global._bb_login_attempts = new Map());
const MAX_ATTEMPTS = 5;
const LOCKOUT_MS = 5 * 60 * 1000; // 5 dakika

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

  const record = store.get(ref_code);
  if (!record) {
    return res.status(404).json({ error: "Bu Referans Kodu ile yayınlanmış veri bulunamadı veya oturum kapandı." });
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

  // Basarili giris - bu ref_code icin sayaci sifirla.
  attempts.delete(ref_code);

  return res.status(200).json({
    success: true,
    ref_code: ref_code,
    timestamp: record.timestamp,
    data: record.data
  });
};
