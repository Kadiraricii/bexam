// Vercel Serverless Function: Senkronizasyon Verisi Alma (POST /api/sync)
const store = global._bb_cloud_store || (global._bb_cloud_store = new Map());

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  try {
    let body = req.body;
    if (typeof body === "string") {
      body = JSON.parse(body);
    }

    if (!body || !body.ref_code) {
      return res.status(400).json({ error: "Eksik parametreler (ref_code)" });
    }

    const key = String(body.ref_code).trim().toUpperCase();

    if (body.action === "delete") {
      store.delete(key);
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

    return res.status(200).json({ success: true, ref_code: key, updated_at: Date.now() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
};
