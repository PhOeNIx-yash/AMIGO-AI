// Offline local connector for LFM3-Omni-2B-Q4_K_M.gguf via amigo-main's bundled llama-server.exe.
// Zero API bills, 100% offline. Uses Node's built-in fetch (Node 22+).
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const EXTRACT_ROOT = path.resolve(__dirname, '..');
const AMIGO_ROOT = path.resolve(EXTRACT_ROOT, '..');

export const LOCAL_LFM_CONFIG = {
  provider: 'openai-compatible',
  baseUrl: process.env.LFM_BASE_URL || 'http://127.0.0.1:8080/v1',
  apiKey: 'local',
  model: 'LFM3-Omni-2B-Q4_K_M',
  serverExe: path.join(AMIGO_ROOT, 'amigo', 'lib', 'llama', 'llama-server.exe'),
  modelPath: path.join(AMIGO_ROOT, 'models', 'LFM3-Omni-2B-Q4_K_M.gguf'),
};

export async function isLfmServerUp(baseUrl = LOCAL_LFM_CONFIG.baseUrl) {
  try {
    const res = await fetch(`${baseUrl}/models`, { signal: AbortSignal.timeout(1500) });
    return res.ok;
  } catch {
    return false;
  }
}

/** Ensures local llama-server.exe is running with LFM3-Omni-2B-Q4_K_M.gguf; returns child process if spawned. */
export async function ensureLfmServer({ port = 8080, gpuLayers = 99, ctxSize = 8192 } = {}) {
  const baseUrl = `http://127.0.0.1:${port}/v1`;
  if (await isLfmServerUp(baseUrl)) return { baseUrl, child: null };
  if (!fs.existsSync(LOCAL_LFM_CONFIG.serverExe)) throw new Error(`Missing ${LOCAL_LFM_CONFIG.serverExe}`);
  if (!fs.existsSync(LOCAL_LFM_CONFIG.modelPath)) throw new Error(`Missing ${LOCAL_LFM_CONFIG.modelPath}`);

  const child = spawn(LOCAL_LFM_CONFIG.serverExe, [
    '-m', LOCAL_LFM_CONFIG.modelPath,
    '--host', '127.0.0.1',
    '--port', String(port),
    '-c', String(ctxSize),
    '-ngl', String(gpuLayers),
    '--jinja',
  ], { stdio: 'ignore', detached: false });

  for (let i = 0; i < 60; i++) {
    if (await isLfmServerUp(baseUrl)) return { baseUrl, child };
    await new Promise((r) => setTimeout(r, 500));
  }
  child.kill();
  throw new Error('Timed out waiting for local LFM3 llama-server to start.');
}

/** Sends an offline chat completion (optionally with OpenAI-format tools) to LFM3-Omni-2B. */
export async function chatLocalLfm({ messages, tools, temperature = 0.7, maxTokens = 1024, baseUrl = LOCAL_LFM_CONFIG.baseUrl }) {
  const body = {
    model: LOCAL_LFM_CONFIG.model,
    messages,
    temperature,
    max_tokens: maxTokens,
    ...(tools?.length ? { tools, tool_choice: 'auto' } : {}),
  };
  const res = await fetch(`${baseUrl}/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${LOCAL_LFM_CONFIG.apiKey}` },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Local LFM error ${res.status}: ${await res.text()}`);
  return res.json();
}

// CLI test: node sidecar/local-lfm.mjs "Tell me a short story"
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const prompt = process.argv.slice(2).join(' ') || 'Say hello in one sentence.';
  const { baseUrl, child } = await ensureLfmServer();
  try {
    const data = await chatLocalLfm({
      baseUrl,
      messages: [
        { role: 'system', content: 'You are a helpful offline desktop assistant.' },
        { role: 'user', content: prompt },
      ],
    });
    console.log(data.choices?.[0]?.message?.content ?? '');
  } finally {
    child?.kill();
  }
}
