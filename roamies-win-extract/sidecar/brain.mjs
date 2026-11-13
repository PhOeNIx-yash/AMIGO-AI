// Side calls next to the conversation: folding old turns into a summary, and the memory pass that learns
// lasting facts about the user. One tool-less turn each, on the cheaper summary model, on this computer's
// own sign-in. What comes back is redacted and checked before anything is stored.
import { Agent } from '@kenkaiiii/gg-agent';
import { REDACTION_MARKER, environmentSecrets, redactText } from '@kenkaiiii/gg-ai';
import { getLowestThinkingLevel, getModel, getSummaryModel } from '@kenkaiiii/gg-core';
import { ClaudeCodeAgent } from './claudecode.mjs';
import { agentEvents } from './agentstream.mjs';

const TIMEOUT_MS = 120_000;
const TIER = { low: 0, medium: 1, high: 2 };

/** gg-core's summary model, unless the current one is already cheaper (Haiku is cheaper than Sonnet). */
export function summaryModelFor(provider, model) {
  const s = getSummaryModel(provider, model), cur = getModel(model);
  if (!s) return model;
  return cur && TIER[cur.costTier] !== undefined && TIER[s.costTier] !== undefined && TIER[cur.costTier] < TIER[s.costTier] ? model : s.id;
}

const LEVELS = ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'];
/** The model's lowest thinking level, but never above the chat's (`cap`; null: thinking off). Some models list
 *  only one level in gg-core (high on Gemini), while the chat already runs them at low. */
export function sideThinking(provider, model, cap) {
  const lowest = getLowestThinkingLevel(provider, model);
  if (!lowest || cap === null) return undefined;
  if (cap === undefined || !LEVELS.includes(cap)) return lowest;
  return LEVELS.indexOf(cap) < LEVELS.indexOf(lowest) ? cap : lowest;
}

async function once({ system, text, provider, model, cc, credsFor, cwd, signal, chatThinking }) {
  const thinking = sideThinking(provider, model, chatThinking);
  const common = { model, system, tools: [], maxTurns: 1, thinking, signal };
  const agent = cc
    ? new ClaudeCodeAgent({ ...common, executable: cc.executable, env: cc.env, cwd })
    : new Agent({
      ...common, provider,
      resolveCredentials: async () => { const c = await credsFor(model); return { apiKey: c.accessToken, ...(c.accountId !== undefined ? { accountId: c.accountId } : {}) }; },
      baseUrl: (await credsFor(model).catch(() => null))?.baseUrl,
    });
  let out = '';
  for await (const e of agentEvents(agent.prompt(text))) {
    if (e.type === 'text_delta') out += e.text;
    else if (e.type === 'error') throw e.error;
  }
  if (!out.trim()) throw new Error('empty reply');
  return out;
}

/**
 * One tool-less turn: { system, text, provider, model (the current one), cc (Claude Code route or null),
 * credsFor(modelId), cwd, signal, chatThinking (the chat's level, null when off) }. Runs on the summary model, then once more on the current model if that fails.
 * Resolves { text, model }.
 */
export async function oneShot(o) {
  const signal = AbortSignal.any([AbortSignal.timeout(TIMEOUT_MS), ...(o.signal ? [o.signal] : [])]);
  const first = summaryModelFor(o.provider, o.model);
  try {
    return { text: await (o.run ?? once)({ ...o, model: first, signal }), model: first };
  } catch (e) {
    if (first === o.model || signal.aborted) throw e;
    console.error(`[brain] ${first} failed (${String(e?.message ?? e).slice(0, 120)}); retrying on ${o.model}`);
    return { text: await (o.run ?? once)({ ...o, signal }), model: o.model };
  }
}

// ---------- prompts ----------

const textOf = (c) => (typeof c === 'string' ? c : (c ?? []).map((p) => (p.type === 'text' ? p.text : p.type === 'image' ? '[image]' : '')).join(' '));
const clip = (s, n) => (s.length > n ? `${s.slice(0, n)}…` : s);

/** The turns as plain text for a side call: what was said, which tools ran, and the start of their results. */
export function transcript(messages, name = 'User', clean = (t) => t) {
  const lines = [];
  for (const m of messages) {
    if (m.role === 'user') { const t = clean(textOf(m.content)).trim(); if (t) lines.push(`${name}: ${t}`); }
    else if (m.role === 'assistant') {
      const parts = typeof m.content === 'string' ? [m.content] : m.content.map((p) => (p.type === 'text' ? p.text : p.type === 'tool_call' ? `[used ${p.name} ${clip(JSON.stringify(p.args ?? {}), 200)}]` : '')).filter(Boolean);
      if (parts.length) lines.push(`Assistant: ${parts.join(' ')}`);
    } else if (m.role === 'tool') {
      for (const r of m.content) lines.push(`[${r.isError ? 'tool error' : 'tool result'}] ${clip(textOf(r.content).replace(/\s+/g, ' '), 300)}`);
    }
  }
  return lines.join('\n');
}

const FACT_RULES = (name) => `Facts are lasting things about ${name} worth knowing in every future conversation: who they are and what they do, their projects, goals, channels, apps and tools, the people they mention (and who those are), their preferences and how they like to work.
- Only what ${name} said themselves or clearly showed. Nothing guessed, nothing the assistant said or did.
- Not small talk, passing moods, questions they asked, one-off tasks, or anything tied to a date or time (reminders and commitments are kept elsewhere).
- Never secrets: no passwords, keys, codes, card, bank or account numbers.
- Skip anything ${name} asked to forget or said was wrong.
- One short sentence each, naming ${name}, under 200 characters.
- Return only facts that are new, or that change a current fact: then set "replaces" to that fact's id. Already known: leave it out. Nothing new: an empty list.`;

const knownFacts = (facts) => (facts.length ? facts.map((f) => `- ${f.id}: ${f.text}`).join('\n') : '(none yet)');

export const foldPrompt = (name) => `You compress the older part of a conversation between ${name} and their desktop companion, so the conversation can carry on without it. You never talk to ${name}; you only write JSON.
Reply with one JSON object and nothing else: {"summary": "...", "facts": [{"text": "...", "replaces": "f123"}]}.
summary: what a companion needs to carry on as if it remembered: what ${name} asked for and what was done (files, apps, pages, results, numbers, names), decisions and preferences stated, anything left open or promised. Merge the earlier summary in if there is one. Plain sentences, oldest first, past tense, at most about 400 words; no greetings, no commentary.
facts: ${FACT_RULES(name)}`;

export const memoryPrompt = (name) => `You read a conversation between ${name} and their desktop companion and pick out what to remember about ${name}. You never talk to ${name}; you only write JSON.
Reply with one JSON object and nothing else: {"facts": [{"text": "...", "replaces": "f123"}]}.
${FACT_RULES(name)}`;

export function foldInput({ facts, summary, turns, name }) {
  return `Current facts (id: text):\n${knownFacts(facts)}\n\n${summary ? `Earlier summary:\n${summary}\n\n` : ''}Turns to fold, oldest first:\n${turns}\n\n${name ? `Write the JSON for ${name} now.` : 'Write the JSON now.'}`;
}
export function memoryInput({ facts, turns }) {
  return `Current facts (id: text):\n${knownFacts(facts)}\n\nConversation, oldest first:\n${turns}\n\nWrite the JSON now.`;
}

// ---------- reading the reply ----------

let secrets;
const redact = (t) => redactText(String(t), { secrets: (secrets ??= environmentSecrets(process.env)) });
// Things redactText doesn't know (it catches keys and tokens): card-like digit runs and named secrets.
const SECRETISH = /\b(?:\d[ -]?){12,19}\b|\b(?:password|passcode|passphrase|pin code|api key|secret key|access token|2fa code|one-time code)\b/i;

/** The model's JSON → { summary, facts: [{ text, replaces? }] }. Throws if there's no usable JSON. */
export function parseBrain(text, { knownIds = [], wantSummary = false } = {}) {
  const raw = String(text ?? '').replace(/^\s*```(?:json)?\s*|\s*```\s*$/g, '');
  const a = raw.indexOf('{'), b = raw.lastIndexOf('}');
  if (a < 0 || b < a) throw new Error('no JSON in the reply');
  const j = JSON.parse(raw.slice(a, b + 1));
  const known = new Set(knownIds);
  const facts = (Array.isArray(j.facts) ? j.facts : []).flatMap((f) => {
    const t = redact(typeof f === 'string' ? f : f?.text ?? '').replace(/\s+/g, ' ').trim();
    if (t.length < 3 || t.length > 240 || t.includes(REDACTION_MARKER) || SECRETISH.test(t)) return [];
    const replaces = typeof f?.replaces === 'string' && known.has(f.replaces) ? f.replaces : undefined;
    return [replaces ? { text: t, replaces } : { text: t }];
  }).slice(0, 20);
  if (!wantSummary) return { facts };
  const summary = redact(typeof j.summary === 'string' ? j.summary : '').trim();
  if (summary.length < 20) throw new Error('no summary in the reply');
  return { summary: summary.slice(0, 8000), facts };
}
