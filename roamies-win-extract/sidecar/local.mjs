// Models served by Ollama on this computer. gg-core finds them and talks to them; this picks the ones Roamies can
// use and keeps their context window honest.
import {
  DEFAULT_LOCAL_ENDPOINTS, clearRuntimeModels, localAuthStorageKey, probeEndpoint, registerRuntimeModels, toModelInfo,
} from '@kenkaiiii/gg-core';

export const OLLAMA = DEFAULT_LOCAL_ENDPOINTS.find((e) => e.id === 'ollama');
export const OLLAMA_KEY = localAuthStorageKey(OLLAMA.id);
// Requests are fitted to half the window, and the system prompt and tools alone are about 12.6k tokens.
export const MIN_CONTEXT = 32_768;

// The window gg-core gives a model of this size (room for the prompt on small windows).
export const withWindow = (info, contextWindow) => ({ ...info, contextWindow, maxOutputTokens: Math.max(512, Math.min(4096, Math.floor(contextWindow / 4))) });

/** Models Roamies can run: it reads screenshots and drives tools, so both are needed. `windows`: measured contexts by id. */
export const localModels = (probes, windows = new Map()) => probes.filter((p) => p.reachable).flatMap((p) => p.models
  .filter((m) => m.supportsImages && m.supportsTools)
  .map((m) => { const info = toModelInfo(m, p.endpoint); return !m.contextWindowKnown && windows.has(info.id) ? withWindow(info, windows.get(info.id)) : info; }));

/**
 * Asks Ollama what it serves and registers the usable models. `save`: also store the `local:ollama` credential
 * (only when it answered). → { ok, reason, models, all } where `all` counts every chat model, usable or not.
 */
export async function connectLocal(auth, { save = false, windows, probe = probeEndpoint } = {}) {
  const p = await probe(OLLAMA, { timeoutMs: 1500 });
  if (!p.reachable) return { ok: false, reason: p.reason ?? 'not running', models: [], all: 0 };
  const models = localModels([p], windows);
  clearRuntimeModels((m) => m.provider === 'local');
  registerRuntimeModels(models);
  if (save) await auth.setLocalEndpoint(OLLAMA.id, OLLAMA.baseUrl);
  return { ok: true, models, all: p.models.length };
}

/** What to tell the user when Ollama serves too small a context, else ''. */
export const contextNote = (window) => (window && window < MIN_CONTEXT
  ? `Ollama gives this model only ${Math.round(window / 1024)}k tokens of memory, too little for me to work well. In Ollama's settings, set Context length to 32k or more (or start it with OLLAMA_CONTEXT_LENGTH=32768), then pick the model again.`
  : '');
