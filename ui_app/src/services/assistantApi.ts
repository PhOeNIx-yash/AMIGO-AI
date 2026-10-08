import { AssistantResponse, BackendConfig, ActionCardItem, AttachmentItem } from "../types";

/**
 * Service to transcribe recorded audio buffer using Amigo local speech or custom STT endpoint
 */
export async function transcribeAudio(
  audioBlob: Blob,
  customTranscribeUrl?: string,
  apiKey?: string
): Promise<string> {
  const arrayBuffer = await audioBlob.arrayBuffer();
  const bytes = new Uint8Array(arrayBuffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  const base64Data = btoa(binary);

  const endpoint = customTranscribeUrl?.trim() || "/api/transcribe";

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (apiKey) {
    headers["Authorization"] = `Bearer ${apiKey}`;
  }

  const response = await fetch(endpoint, {
    method: "POST",
    headers,
    body: JSON.stringify({
      audioData: base64Data,
      mimeType: audioBlob.type || "audio/webm",
    }),
  });

  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.error || `Failed transcribing audio (HTTP ${response.status})`);
  }

  const data = await response.json();
  return data.transcription || data.text || data.transcript || "";
}

function isSecureOrLocalEndpoint(url: string): boolean {
  if (url.startsWith("/")) return true;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:") return true;
    if (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1" || parsed.hostname === "::1") return true;
    return false;
  } catch {
    return false;
  }
}

function applyCustomHeaders(headers: Record<string, string>, rawJson?: string) {
  if (!rawJson || typeof rawJson !== "string") return;
  try {
    const parsed = JSON.parse(rawJson);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      for (const [key, value] of Object.entries(parsed)) {
        if (
          typeof key === "string" &&
          key.trim() &&
          (typeof value === "string" || typeof value === "number" || typeof value === "boolean")
        ) {
          headers[key.trim()] = String(value);
        }
      }
    }
  } catch (_) {}
}

/**
 * Test connectivity with user's backend endpoint
 */
export async function testBackendConnection(config: BackendConfig): Promise<{
  success: boolean;
  latencyMs: number;
  message: string;
  statusCode?: number;
}> {
  const startTime = performance.now();
  const endpoint = config.endpointUrl?.trim() || "/api/assistant/process";

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (config.apiKey && isSecureOrLocalEndpoint(endpoint)) {
    headers["Authorization"] = `Bearer ${config.apiKey}`;
  }

  applyCustomHeaders(headers, config.customHeaders);

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers,
      body: JSON.stringify({
        prompt: "ping_health_check",
        isHealthCheck: true,
        timestamp: Date.now(),
      }),
    });

    const latencyMs = Math.round(performance.now() - startTime);

    if (response.ok) {
      return {
        success: true,
        latencyMs,
        message: `Backend online (${latencyMs}ms)`,
        statusCode: response.status,
      };
    } else {
      return {
        success: false,
        latencyMs,
        message: `Backend returned HTTP ${response.status}`,
        statusCode: response.status,
      };
    }
  } catch (err: any) {
    const latencyMs = Math.round(performance.now() - startTime);
    return {
      success: false,
      latencyMs,
      message: err.message || "Connection refused / CORS error",
    };
  }
}

/**
 * Execute an action callback to the user's backend
 */
export async function executeBackendAction(
  action: ActionCardItem,
  backendConfig?: BackendConfig
): Promise<any> {
  if (!backendConfig) return { success: true };

  const endpoint =
    backendConfig.actionWebhookUrl?.trim() ||
    (backendConfig.endpointUrl?.trim()
      ? `${backendConfig.endpointUrl.trim().replace(/\/+$/, "")}/action`
      : null);

  if (!endpoint) {
    return { success: true, local: true };
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (backendConfig.apiKey && isSecureOrLocalEndpoint(endpoint)) {
    headers["Authorization"] = `Bearer ${backendConfig.apiKey}`;
  }

  applyCustomHeaders(headers, backendConfig.customHeaders);

  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers,
      body: JSON.stringify({
        tool: action.id,
        payload: action.payload,
        original_prompt: action.originalPrompt || "",
      }),
    });
    return await res.json().catch(() => ({ success: res.ok }));
  } catch (err) {
    console.warn("Action execution webhook error:", err);
    return { success: false, error: String(err) };
  }
}

/**
 * Process voice / text command with configured backend API
 * Seamlessly normalizes any backend response structure
 */
export async function processVoiceCommand(
  prompt: string,
  backendConfig?: BackendConfig,
  context?: Record<string, any>,
  attachment?: AttachmentItem
): Promise<AssistantResponse> {
  const endpoint = backendConfig?.endpointUrl?.trim() || "/api/assistant/process";

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (backendConfig?.apiKey && isSecureOrLocalEndpoint(endpoint)) {
    headers["Authorization"] = `Bearer ${backendConfig.apiKey}`;
  }

  applyCustomHeaders(headers, backendConfig?.customHeaders);

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers,
      body: JSON.stringify({
        prompt,
        context: context || {},
        attachment: attachment
          ? {
              filename: attachment.filename,
              filepath: attachment.filepath,
              fileType: attachment.fileType,
              size: attachment.size,
              extractedPreview: attachment.extractedPreview,
            }
          : undefined,
        timestamp: Date.now(),
      }),
    });

    if (response.ok) {
      const rawData = await response.json();
      return normalizeBackendResponse(rawData, prompt);
    }
  } catch (err) {
    console.warn("Backend request failed, falling back to local normalizer:", err);
  }

  return generateDynamicResponse(prompt);
}

/**
 * Upload an attached file (PDF, image, document) to the backend for storage, indexing, and OCR
 */
export async function uploadFileToBackend(file: File): Promise<{
  success: boolean;
  filename: string;
  filepath: string;
  fileType: "pdf" | "image" | "document" | "generic";
  size: number;
  extractedPreview?: string;
  error?: string;
}> {
  // Try multipart/form-data first
  try {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/api/upload", {
      method: "POST",
      body: formData,
    });

    if (res.ok) {
      return await res.json();
    }
  } catch (e) {
    console.warn("FormData upload failed, trying base64 fallback:", e);
  }

  // Fallback: convert to base64 JSON payload
  const arrayBuffer = await file.arrayBuffer();
  const bytes = new Uint8Array(arrayBuffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  const base64Data = btoa(binary);

  const res = await fetch("/api/upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filename: file.name,
      mimeType: file.type,
      size: file.size,
      fileData: base64Data,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Upload failed (HTTP ${res.status})`);
  }

  return await res.json();
}

/**
 * Normalizes any backend structure (standard, FastAPI, Flask, LangChain, OpenAI format, or simple text)
 * into standard AssistantResponse format
 */
export function normalizeBackendResponse(raw: any, originalPrompt: string): AssistantResponse {
  if (!raw || typeof raw !== "object") {
    const textOutput = String(raw || originalPrompt);
    return {
      speechReply: textOutput,
      displayTitle: originalPrompt,
      intent: "custom_response",
      requiresDisambiguation: false,
      actionCards: [],
      executionSummary: {
        status: "completed",
        headline: "Completed",
        details: textOutput,
      },
    };
  }

  // 1. Direct standard schema
  if (raw.speechReply || raw.executionSummary || raw.actionCards) {
    let actionCards: ActionCardItem[] = Array.isArray(raw.actionCards)
      ? raw.actionCards.map((c: any, i: number) => ({
          id: c.id || `act-${i + 1}`,
          type: c.type || "general",
          title: c.title || c.name || "Action item",
          subtitle: c.subtitle || c.description,
          selected: c.selected !== undefined ? Boolean(c.selected) : true,
          payload: c.payload,
          url: c.url,
          badge: c.badge,
          actionType: c.actionType || "toggle",
        }))
      : [];

    // Auto-synthesize timer/stopwatch card if backend omitted actionCards
    if (
      actionCards.length === 0 &&
      (raw.intent === "set_timer" ||
        raw.intent === "timer" ||
        raw.intent === "stopwatch" ||
        /\b(timer|countdown|stopwatch)\b/i.test(originalPrompt))
    ) {
      const isStopwatch = raw.intent === "stopwatch" || originalPrompt.toLowerCase().includes("stopwatch");
      const seconds = parseSecondsFromPrompt(originalPrompt);
      const mins = Math.floor(seconds / 60);
      const secs = seconds % 60;
      actionCards = [
        {
          id: `act-timer-${Date.now()}`,
          type: isStopwatch ? "stopwatch" : "timer",
          title: isStopwatch ? "Stopwatch" : "Countdown Timer",
          subtitle: isStopwatch ? "Active Stopwatch" : `${mins}m ${secs}s countdown`,
          selected: true,
          badge: isStopwatch ? "Stopwatch" : "Timer",
          payload: {
            tool: isStopwatch ? "stopwatch" : "set_timer",
            duration_seconds: seconds,
            seconds,
            mode: isStopwatch ? "stopwatch" : "timer",
            label: isStopwatch ? "Stopwatch" : "Timer",
          },
        },
      ];
    }

    return {
      speechReply: raw.speechReply || raw.text || raw.message || raw.reply || "",
      displayTitle: raw.displayTitle || originalPrompt,
      intent: raw.intent || "general_task",
      params: raw.params || raw.metadata?.params,
      requiresDisambiguation: Boolean(raw.requiresDisambiguation),
      disambiguationQuestion: raw.disambiguationQuestion,
      actionCards,
      contactMatches: Array.isArray(raw.contactMatches) ? raw.contactMatches : undefined,
      executionSummary: {
        status: raw.executionSummary?.status || "completed",
        headline: raw.executionSummary?.headline || "Executed Task",
        details: raw.executionSummary?.details || raw.speechReply || raw.reply || "Task processed successfully.",
        secondaryDetails: raw.executionSummary?.secondaryDetails,
        rawOutput: raw.executionSummary?.rawOutput || raw,
      },
      url: raw.url || raw.executionSummary?.rawOutput?.url,
      metadata: raw.metadata,
    };
  }

  // 2. Generic LLM / Agent format ({ response, actions, result, text, message })
  const speech = raw.response || raw.reply || raw.text || raw.message || raw.output || raw.answer || "";
  const actionsList: ActionCardItem[] = [];

  if (Array.isArray(raw.actions) || Array.isArray(raw.tools) || Array.isArray(raw.intent_actions)) {
    const list = (raw.actions || raw.tools || raw.intent_actions).filter(
      (item: any) => item.type !== "web_search" && item.tool !== "web_search" && item.name !== "web_search"
    );
    list.forEach((item: any, idx: number) => {
      actionsList.push({
        id: item.id || `act-${idx}`,
        type: item.type || "general",
        title: item.title || item.name || item.action || "Execute action",
        subtitle: item.subtitle || item.description || item.params ? JSON.stringify(item.params) : undefined,
        selected: item.selected !== false,
        payload: item.payload || item.params,
        url: item.url,
        badge: item.badge || item.status,
        actionType: item.actionType || "button",
      });
    });
  }

  return {
    speechReply: speech,
    displayTitle: raw.title || originalPrompt,
    intent: raw.intent || "custom_intent",
    requiresDisambiguation: Boolean(raw.disambiguationRequired || raw.requiresDisambiguation),
    disambiguationQuestion: raw.disambiguationQuestion || raw.question,
    actionCards: actionsList,
    contactMatches: Array.isArray(raw.contacts) ? raw.contacts : undefined,
    executionSummary: {
      status: raw.status || "completed",
      headline: raw.headline || "Task Completed",
      details: speech || "Backend returned response.",
      secondaryDetails: raw.secondaryDetails || (raw.data ? JSON.stringify(raw.data, null, 2) : undefined),
      rawOutput: raw,
    },
    url: raw.url,
    metadata: raw,
  };
}

function parseSecondsFromPrompt(prompt: string): number {
  let p = prompt.toLowerCase();
  const wordMap: Record<string, number> = {
    zero: 0, a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5,
    six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
    thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17,
    eighteen: 18, nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50,
    sixty: 60, seventy: 70, eighty: 80, ninety: 90, hundred: 100,
    half: 0.5, quarter: 0.25,
  };

  p = p.replace(/\bhalf\s+(?:an?\s+)?hours?\b/g, "30 minutes");
  p = p.replace(/\bquarter\s+(?:of\s+an?\s+)?hours?\b/g, "15 minutes");
  p = p.replace(/\bhalf\s+(?:a\s+)?minutes?\b/g, "30 seconds");

  for (const [w, n] of Object.entries(wordMap)) {
    p = p.replace(new RegExp(`\\b${w}\\b`, "g"), String(n));
  }

  let total = 0;
  let matched = false;

  const patterns: [RegExp, number][] = [
    [/(\d+(?:\.\d+)?)\s*(?:days?|d)\b/g, 86400],
    [/(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|hr|h)\b/g, 3600],
    [/(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|min|m)\b/g, 60],
    [/(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|sec|s)\b/g, 1],
  ];

  for (const [regex, multiplier] of patterns) {
    let match: RegExpExecArray | null;
    while ((match = regex.exec(p)) !== null) {
      total += parseFloat(match[1]) * multiplier;
      matched = true;
    }
  }

  if (matched && total > 0) return Math.round(total);

  const cleanNum = p.replace(/[^\d.]/g, "");
  if (cleanNum && !isNaN(Number(cleanNum))) {
    return Math.round(Number(cleanNum));
  }

  return 60;
}

function generateDynamicResponse(prompt: string): AssistantResponse {
  const lower = prompt.toLowerCase();

  // Timer & Countdown
  if (/\b(timer|countdown|stopwatch)\b/i.test(lower)) {
    const isStopwatch = lower.includes("stopwatch");
    const seconds = parseSecondsFromPrompt(prompt);
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;

    return {
      speechReply: isStopwatch ? "Starting stopwatch." : `Setting a timer for ${mins > 0 ? `${mins} minutes` : `${secs} seconds`}.`,
      displayTitle: prompt,
      intent: isStopwatch ? "stopwatch" : "set_timer",
      requiresDisambiguation: false,
      actionCards: [
        {
          id: `act-timer-${Date.now()}`,
          type: isStopwatch ? "stopwatch" : "timer",
          title: isStopwatch ? "Stopwatch" : "Countdown Timer",
          subtitle: isStopwatch ? "Active Stopwatch" : `${mins}m ${secs}s countdown`,
          selected: true,
          badge: isStopwatch ? "Stopwatch" : "Timer",
          payload: {
            tool: isStopwatch ? "stopwatch" : "set_timer",
            duration_seconds: seconds,
            seconds,
            mode: isStopwatch ? "stopwatch" : "timer",
            label: isStopwatch ? "Stopwatch" : "Timer",
          },
        },
      ],
      executionSummary: {
        status: "completed",
        headline: isStopwatch ? "Stopwatch Active" : "Timer Set",
        details: isStopwatch ? "Stopwatch running." : `Timer active for ${mins}m ${secs}s.`,
      },
    };
  }

  // Weather
  if (/\b(weather|temperature|forecast|climate)\b/i.test(lower)) {
    const cityMatch = prompt.match(/\b(?:in|of|for|at)\s+([a-zA-Z\s]+)/i);
    const targetCity = cityMatch ? cityMatch[1].trim() : "";

    return {
      speechReply: targetCity ? `Checking the live weather in ${targetCity}.` : `Checking the local live weather.`,
      displayTitle: prompt,
      intent: "get_weather",
      requiresDisambiguation: false,
      actionCards: [
        {
          id: `act-weather-${Date.now()}`,
          type: "weather",
          title: targetCity ? `Weather in ${targetCity}` : "Local Weather",
          subtitle: "Live Meteorological Telemetry",
          selected: true,
          badge: "Weather",
          payload: {
            tool: "get_weather",
            city: targetCity,
          },
        },
      ],
      executionSummary: {
        status: "completed",
        headline: "Weather Telemetry",
        details: targetCity ? `Fetching live atmospheric data for ${targetCity}.` : "Fetching live meteorological conditions.",
      },
    };
  }

  return {
    speechReply: `Processing: "${prompt}".`,
    displayTitle: prompt,
    intent: "general_action",
    requiresDisambiguation: false,
    actionCards: [
      {
        id: `act-${Date.now()}`,
        type: "general",
        title: prompt,
        subtitle: "Voice Assistant Action",
        selected: true,
      },
    ],
    executionSummary: {
      status: "completed",
      headline: "Command Processed",
      details: `Processed: "${prompt}".`,
    },
  };
}

export interface IndexerProgressData {
  is_indexing?: boolean;
  files_total?: number;
  files_processed?: number;
  files_indexed?: number;
  files_skipped?: number;
  files_left?: number;
  progress_percent?: number;
  current_file?: string;
  status_message?: string;
  last_run?: string | null;
  last_duration_seconds?: number;
}

export interface RagStatusData {
  conversations?: number;
  user_facts?: number;
  documents?: number;
  emails?: number;
  calendar?: number;
  total?: number;
  is_indexing?: boolean;
  indexer?: IndexerProgressData;
}

export interface ApiFetchOptions extends RequestInit {
  timeoutMs?: number;
}

/**
 * Universal robust API fetch wrapper with timeout, abort handling, and standardized error parsing
 */
export async function apiFetch<T = any>(
  endpoint: string,
  options: ApiFetchOptions = {}
): Promise<T> {
  const { timeoutMs = 20000, signal, ...fetchOpts } = options;
  const controller = new AbortController();

  let timeoutId: any = null;
  if (timeoutMs > 0) {
    timeoutId = setTimeout(() => {
      controller.abort(new Error(`Request timed out after ${timeoutMs}ms`));
    }, timeoutMs);
  }

  if (signal) {
    signal.addEventListener("abort", () => {
      controller.abort(signal.reason);
      if (timeoutId) clearTimeout(timeoutId);
    });
  }

  try {
    const res = await fetch(endpoint, {
      ...fetchOpts,
      signal: controller.signal,
    });
    if (timeoutId) clearTimeout(timeoutId);

    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      const message =
        errBody.error ||
        errBody.message ||
        `Request to ${endpoint} failed with HTTP ${res.status}`;
      const error: any = new Error(message);
      error.status = res.status;
      error.data = errBody;
      throw error;
    }

    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return (await res.json()) as T;
    }
    return (await res.text()) as unknown as T;
  } catch (err: any) {
    if (timeoutId) clearTimeout(timeoutId);
    throw err;
  }
}

export async function fetchRagStatus(signal?: AbortSignal): Promise<RagStatusData> {
  try {
    return await apiFetch<RagStatusData>("/api/rag/status", {
      signal,
      timeoutMs: 8000,
    });
  } catch (e) {
    return { total: 0, documents: 0, conversations: 0, is_indexing: false };
  }
}

export async function triggerRagReindex(force: boolean = true): Promise<{ status: string; message: string }> {
  return await apiFetch<{ status: string; message: string }>("/api/rag/reindex", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
    timeoutMs: 15000,
  });
}

export async function fetchAssistantSettings(signal?: AbortSignal): Promise<any> {
  return await apiFetch("/api/settings", { signal, timeoutMs: 10000 });
}

export async function saveAssistantSettings(settings: any): Promise<any> {
  return await apiFetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
    timeoutMs: 12000,
  });
}

export async function fetchAssistantHistory(signal?: AbortSignal): Promise<any> {
  return await apiFetch("/api/history", { signal, timeoutMs: 10000 });
}

export async function clearAssistantHistory(): Promise<any> {
  return await apiFetch("/api/clear-memory", {
    method: "POST",
    timeoutMs: 10000,
  });
}

export async function executeAssistantAction(payload: Record<string, any>): Promise<any> {
  return await apiFetch("/api/action/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 15000,
  });
}

