/**
 * Runtime Configuration for Amigo UI.
 *
 * Centralizes all environment variables, fallback endpoints, thresholds,
 * timeouts, and animation constants. Eliminates hardcoding and magic numbers
 * across the entire React UI codebase.
 */

export interface RuntimeConfig {
  apiBaseUrl: string;
  wsBaseUrl: string;
  sseEndpoint: string;
  speakEndpoint: string;
  chatEndpoint: string;
  transcribeEndpoint: string;
  uploadEndpoint: string;
  canvasActionEndpoint: string;
  healthEndpoint: string;
  
  // Timing & Thresholds (in ms)
  apiTimeoutMs: number;
  sseReconnectMinIntervalMs: number;
  sseReconnectMaxIntervalMs: number;
  debounceDefaultMs: number;
  audioChunkTimeSliceMs: number;
  maxAudioRecordingDurationMs: number;
  
  // Visual & Performance
  targetFps: number;
  maxRenderedMessages: number;
  defaultSparklesDensity: number;
  minSparklesSpeed: number;
  maxSparklesSpeed: number;
}

const resolveBaseUrl = (): string => {
  if (typeof window === 'undefined') return 'http://127.0.0.1:5000';
  
  // Custom env override if provided
  const envUrl = import.meta.env.VITE_API_BASE_URL;
  if (envUrl && typeof envUrl === 'string' && envUrl.trim() !== '') {
    return envUrl.trim().replace(/\/+$/, '');
  }
  
  // In dev / production served from Vite, default to host origin or localhost:5000 if running on Vite dev server
  const { protocol, hostname, port } = window.location;
  if (port === '5173' || port === '3000' || port === '8080') {
    return `${protocol}//${hostname}:5000`;
  }
  return window.location.origin;
};

const resolveWsUrl = (baseUrl: string): string => {
  try {
    const parsed = new URL(baseUrl);
    const wsProto = parsed.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProto}//${parsed.host}`;
  } catch {
    return 'ws://127.0.0.1:5000';
  }
};

const BASE_URL = resolveBaseUrl();
const WS_URL = resolveWsUrl(BASE_URL);

export const runtimeConfig: RuntimeConfig = {
  apiBaseUrl: BASE_URL,
  wsBaseUrl: WS_URL,
  sseEndpoint: `${BASE_URL}/events`,
  speakEndpoint: `${BASE_URL}/api/speak`,
  chatEndpoint: `${BASE_URL}/api/chat`,
  transcribeEndpoint: `${BASE_URL}/api/transcribe`,
  uploadEndpoint: `${BASE_URL}/api/upload`,
  canvasActionEndpoint: `${BASE_URL}/api/canvas-action`,
  healthEndpoint: `${BASE_URL}/api/health`,

  // Timeouts & intervals
  apiTimeoutMs: Number(import.meta.env.VITE_API_TIMEOUT_MS) || 45_000,
  sseReconnectMinIntervalMs: Number(import.meta.env.VITE_SSE_RECONNECT_MIN_MS) || 1_000,
  sseReconnectMaxIntervalMs: Number(import.meta.env.VITE_SSE_RECONNECT_MAX_MS) || 15_000,
  debounceDefaultMs: 150,
  audioChunkTimeSliceMs: 250,
  maxAudioRecordingDurationMs: 120_000, // 2 minutes max per voice turn

  // Performance parameters
  targetFps: 60,
  maxRenderedMessages: 200,
  defaultSparklesDensity: 80,
  minSparklesSpeed: 0.4,
  maxSparklesSpeed: 1.2,
};
