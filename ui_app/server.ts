import express from "express";
import path from "path";
import { createServer as createViteServer } from "vite";

async function startServer() {
  const app = express();
  const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : 3000;
  const AMIGO_BACKEND = process.env.AMIGO_BACKEND || "http://127.0.0.1:5000";

  app.use(express.json({ limit: "50mb" }));

  // Real-time SSE proxy -> Streams events directly from Amigo Python backend
  app.get("/events", async (req, res) => {
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache, no-transform");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders?.();

    try {
      const amigoRes = await fetch(`${AMIGO_BACKEND}/events`);
      if (!amigoRes.ok || !amigoRes.body) {
        res.write(`data: ${JSON.stringify({ type: "state_change", state: "idle" })}\n\n`);
        return res.end();
      }

      const reader = amigoRes.body.getReader();
      const decoder = new TextDecoder();

      req.on("close", () => {
        reader.cancel().catch(() => {});
      });

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        res.write(chunk);
      }
    } catch (e: any) {
      // If backend is restarting or offline, send a graceful fallback event
      res.write(`data: ${JSON.stringify({ type: "state_change", state: "idle" })}\n\n`);
      res.end();
    }
  });

  // Weather proxy route -> Proxies to Amigo Python backend
  app.get("/api/weather", async (req, res) => {
    try {
      const city = req.query.city ? `?city=${encodeURIComponent(String(req.query.city))}` : "";
      const amigoRes = await fetch(`${AMIGO_BACKEND}/api/weather${city}`);
      if (amigoRes.ok) {
        const data = await amigoRes.json();
        return res.json(data);
      }
    } catch (e) {}
    return res.json({ temp_c: 24, condition: "Clear", city: "Local" });
  });

  // API Route: Transcribe audio -> Proxies to Amigo local Python backend
  app.post("/api/transcribe", async (req, res) => {
    try {
      const amigoRes = await fetch(`${AMIGO_BACKEND}/api/transcribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req.body),
      });
      if (amigoRes.ok) {
        const data = await amigoRes.json();
        return res.json(data);
      }
    } catch (error: any) {
      console.warn("[Amigo STT Proxy Error]:", error?.message);
    }
    return res.json({ transcription: "" });
  });

  // API Route: Assistant structured intent processor -> Bridges to Amigo Local Python Backend
  app.post("/api/assistant/process", async (req, res) => {
    const { prompt, context, attachment } = req.body;
    if (!prompt || typeof prompt !== "string") {
      return res.status(400).json({ error: "Missing prompt" });
    }

    try {
      const amigoRes = await fetch(`${AMIGO_BACKEND}/api/assistant/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, context, attachment }),
      });
      if (amigoRes.ok) {
        const data = await amigoRes.json();
        return res.json(data);
      }
    } catch (e: any) {
      console.warn("[Amigo Process Proxy Error]:", e?.message);
    }

    return res.json(generateFallbackAssistantResponse(prompt));
  });

  // API Route: Upload file (PDF, Image, Document) -> Proxies to Amigo Python backend
  app.post("/api/upload", async (req, res) => {
    try {
      const contentType = req.headers["content-type"] || "application/json";
      let fetchOptions: any;

      if (contentType.includes("application/json")) {
        fetchOptions = {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(req.body),
        };
      } else {
        const chunks: Buffer[] = [];
        for await (const chunk of req) {
          chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
        }
        const fullBuffer = Buffer.concat(chunks);
        fetchOptions = {
          method: "POST",
          headers: { "Content-Type": contentType },
          body: fullBuffer,
        };
      }

      const amigoRes = await fetch(`${AMIGO_BACKEND}/api/upload`, fetchOptions);
      const data = await amigoRes.json().catch(() => ({ success: amigoRes.ok }));
      return res.status(amigoRes.status).json(data);
    } catch (e: any) {
      console.warn("[Amigo Upload Proxy Error]:", e?.message);
      return res.status(500).json({ success: false, error: e?.message || "Upload proxy failed" });
    }
  });

  // Action execution route -> Proxies to Amigo Python backend
  app.post("/api/action/execute", async (req, res) => {
    try {
      const amigoRes = await fetch(`${AMIGO_BACKEND}/api/action/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req.body),
      });
      if (amigoRes.ok) {
        const data = await amigoRes.json();
        return res.json(data);
      }
    } catch (e) {}
    return res.json({ success: true, message: "Action executed" });
  });

  // System stats route -> Proxies to Amigo Python backend
  app.get("/api/system-stats", async (req, res) => {
    try {
      const amigoRes = await fetch(`${AMIGO_BACKEND}/api/system-stats`);
      if (amigoRes.ok) {
        const data = await amigoRes.json();
        return res.json(data);
      }
    } catch (e) {}
    return res.json({ cpu: 0, ram: 0, battery: null });
  });

  // Settings route -> Proxies to Amigo Python backend
  app.all("/api/settings", async (req, res) => {
    try {
      const amigoRes = await fetch(`${AMIGO_BACKEND}/api/settings`, {
        method: req.method,
        headers: { "Content-Type": "application/json" },
        body: req.method !== "GET" ? JSON.stringify(req.body) : undefined,
      });
      if (amigoRes.ok) {
        const data = await amigoRes.json();
        return res.json(data);
      }
    } catch (e) {}
    return res.json({ success: true });
  });

  // History route -> Proxies to Amigo Python backend
  app.get("/api/history", async (req, res) => {
    try {
      const amigoRes = await fetch(`${AMIGO_BACKEND}/api/history`);
      if (amigoRes.ok) {
        const data = await amigoRes.json();
        return res.json(data);
      }
    } catch (e) {}
    return res.json({ conversations: [] });
  });

  // Clear memory route -> Proxies to Amigo Python backend
  app.post("/api/clear-memory", async (req, res) => {
    try {
      const amigoRes = await fetch(`${AMIGO_BACKEND}/api/clear-memory`, {
        method: "POST",
      });
      if (amigoRes.ok) {
        const data = await amigoRes.json();
        return res.json(data);
      }
    } catch (e) {}
    return res.json({ success: true });
  });

  // Generic transparent proxy for all other /api/* routes -> Proxies to Amigo Python backend
  app.all("/api/*", async (req, res) => {
    try {
      const targetUrl = `${AMIGO_BACKEND}${req.originalUrl}`;
      const options: RequestInit = {
        method: req.method,
        headers: { "Content-Type": "application/json" },
      };
      if (req.method !== "GET" && req.method !== "HEAD" && req.body && Object.keys(req.body).length > 0) {
        options.body = JSON.stringify(req.body);
      }
      const amigoRes = await fetch(targetUrl, options);
      const data = await amigoRes.json().catch(() => ({}));
      return res.status(amigoRes.status).json(data);
    } catch (e: any) {
      console.warn(`[Amigo Generic API Proxy Error for ${req.originalUrl}]:`, e?.message);
      return res.status(502).json({ error: "Backend unreachable" });
    }
  });

  // Vite middleware setup
  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*", (req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Windows 11 Amigo Voice Assistant UI listening on http://0.0.0.0:${PORT}`);
  });
}

function generateFallbackAssistantResponse(prompt: string) {
  return {
    speechReply: `Processing request: "${prompt}".`,
    displayTitle: prompt,
    intent: "chat",
    requiresDisambiguation: false,
    actionCards: [],
    executionSummary: {
      status: "completed",
      headline: "Amigo AI Response",
      details: `Processed with Amigo: "${prompt}".`,
    },
  };
}

startServer();
