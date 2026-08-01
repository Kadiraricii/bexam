// Vercel Serverless Function: Referans Kodu & PIN ile Durum Sorgulama (POST/GET /api/view)
const store = global._bb_cloud_store || (global._bb_cloud_store = new Map());

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

  const record = store.get(ref_code);
  if (!record) {
    return res.status(404).json({ error: "Bu Referans Kodu ile yayınlanmış veri bulunamadı veya oturum kapandı." });
  }

  if (record.pin !== pin) {
    return res.status(401).json({ error: "Hatalı PIN Kodu! Lütfen masaüstü uygulamasındaki 4 haneli PIN kodunu kontrol edin." });
  }

  return res.status(200).json({
    success: true,
    ref_code: ref_code,
    timestamp: record.timestamp,
    data: record.data
  });
};
