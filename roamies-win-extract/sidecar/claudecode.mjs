// "Sign in with Claude" runs replies through Claude Code: it drives the model loop with our system prompt, and our
// tools are served to it as an in-process MCP server. Ported from GG Coder's core/claude-code (loop, events,
// tool-bridge), cut to what Roamies uses. ClaudeCodeAgent has the surface sidekick.mjs uses of gg-agent's Agent.
import { query as sdkQuery, startup as sdkStartup } from '@anthropic-ai/claude-agent-sdk';
import { AgentStream, argumentHints, capToolResults, unknownToolMessage } from '@kenkaiiii/gg-agent';
import { environmentSecrets, redactValue, resolveToolSchema } from '@kenkaiiii/gg-ai';
import { recordClaudeCodeRateLimit } from '@kenkaiiii/gg-core';
import { ZodError, prettifyError } from 'zod';
import crypto from 'node:crypto';

export const MCP_SERVER = 'gg';
const TOOL_PREFIX = `mcp__${MCP_SERVER}__`;
const DEFAULT_TOOL_TIMEOUT_MS = 300_000;
// Our own per-tool timeout must fire first so its specific message reaches the model.
const SERVER_TIMEOUT_MARGIN_MS = 60_000;
const MAX_SEED_CHARS = 400_000;
const INTERRUPT_GRACE_MS = 5_000; // how long interrupt() gets before the process is killed
const INTERRUPTED_NOTE = 'Interrupted by the user before this tool call finished.';
const STDERR_TAIL_CHARS = 4_000;
const SYNTHETIC_MODEL = '<synthetic>';
const SPARE_IDLE_MS = 5 * 60_000; // a warm process unused this long is let go (it holds ~250 MB)

export const stripToolPrefix = (name) => (name.startsWith(TOOL_PREFIX) ? name.slice(TOOL_PREFIX.length) : name);
export const effortForThinking = (level) => (!level ? undefined : level === 'ultra' ? 'max' : level);

// ---------- events: SDK messages → gg-agent events and our transcript ----------
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
export function toUsage(raw) {
  const u = raw ?? {};
  const usage = { inputTokens: num(u.input_tokens), outputTokens: num(u.output_tokens) };
  const reasoning = num(u.output_tokens_details?.thinking_tokens);
  if (reasoning) usage.reasoningTokens = reasoning;
  if (num(u.cache_read_input_tokens)) usage.cacheRead = num(u.cache_read_input_tokens);
  if (num(u.cache_creation_input_tokens)) usage.cacheWrite = num(u.cache_creation_input_tokens);
  return usage;
}
function addUsage(total, u) {
  total.inputTokens += u.inputTokens; total.outputTokens += u.outputTokens;
  for (const k of ['reasoningTokens', 'cacheRead', 'cacheWrite']) if (u[k]) total[k] = (total[k] ?? 0) + u[k];
}
const STOP_REASONS = new Set(['end_turn', 'tool_use', 'max_tokens', 'pause_turn', 'stop_sequence', 'refusal']);
const toStopReason = (raw) => (STOP_REASONS.has(raw) ? raw : 'end_turn');

function fromSdkBlock(b) {
  switch (b.type) {
    case 'text': return { type: 'text', text: String(b.text ?? '') };
    case 'thinking': return { type: 'thinking', text: String(b.thinking ?? ''), ...(typeof b.signature === 'string' ? { signature: b.signature } : {}) };
    case 'tool_use': return { type: 'tool_call', id: String(b.id), name: stripToolPrefix(String(b.name)), args: b.input ?? {} };
    case 'server_tool_use': return { type: 'server_tool_call', id: String(b.id), name: String(b.name), input: b.input };
    default:
      if (typeof b.tool_use_id === 'string') return { type: 'server_tool_result', toolUseId: b.tool_use_id, resultType: String(b.type), data: b };
      return { type: 'raw', data: b, origin: 'claude-code' };
  }
}
// A tool result Claude Code produced itself (the call never reached our bridge, e.g. it denied it).
function fromSdkToolResult(b) {
  const raw = b.content;
  const content = typeof raw === 'string' ? raw : Array.isArray(raw)
    ? raw.map((p) => (p.type === 'image' && p.source ? { type: 'image', mediaType: String(p.source.media_type ?? 'image/png'), data: String(p.source.data ?? '') } : { type: 'text', text: String(p.text ?? '') }))
    : '';
  return { type: 'tool_result', toolCallId: String(b.tool_use_id), content, ...(b.is_error ? { isError: true } : {}) };
}
const resultText = (c) => (typeof c === 'string' ? c : c.map((p) => (p.type === 'text' ? p.text : `[${p.type}]`)).join('\n'));

export function toSdkUserContent(content) {
  if (typeof content === 'string') return [{ type: 'text', text: content }];
  return content.map((p) => {
    if (p.type === 'text') return { type: 'text', text: p.text };
    if (p.type === 'image') return { type: 'image', source: { type: 'base64', media_type: p.mediaType, data: p.data } };
    return { type: 'text', text: `[${p.type} attachment omitted: not supported here]` };
  });
}
const toSdkUserMessage = (content) => ({ type: 'user', message: { role: 'user', content }, parent_tool_use_id: null });

// A tool call copied into a reply as text by an earlier session: not part of what was said.
const ECHOED = /^[ \t]*(?:\[called [\w.-]+(?: [^\n]*)?\]|Tool (?:use|result): [^\n]*)[ \t]*$/gm;
// One message as transcript entries: what was said under its role; each tool call and result as its own entry, never
// inside the assistant's words (models copied "[called …]" lines from there into their replies, and they were spoken).
function messageEntries(m) {
  const label = ROLE_LABEL[m.role];
  if (typeof m.content === 'string') return [[label, m.content.replace(ECHOED, '')]];
  const out = [], said = [];
  const flush = () => { if (said.length) out.push([label, said.join('\n').replace(ECHOED, '')]); said.length = 0; };
  for (const p of m.content) {
    if (p.type === 'text') said.push(p.text);
    else if (p.type === 'image' || p.type === 'video') said.push(`(${p.type})`);
    else if (p.type === 'tool_call') { flush(); out.push(['Tool use', `${p.name} ${JSON.stringify(p.args)}`]); }
    else if (p.type === 'tool_result') { flush(); out.push([p.isError ? 'Tool error' : 'Tool result', resultText(p.content)]); }
  }
  flush();
  return out;
}
const ROLE_LABEL = { system: 'System', user: 'User', assistant: 'Assistant', tool: 'Tool' };
// Our history as one text block, for a fresh Claude Code session that never saw it. Keeps the newest messages.
export function renderTranscript(messages, maxChars) {
  const entries = [];
  let used = 0, dropped = 0;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === 'system') continue;
    const mine = messageEntries(m).map(([who, text]) => [who, text.trim()]).filter(([, text]) => text).map(([who, text]) => `${who}: ${text}`).join('\n\n');
    if (!mine) continue;
    if (used + mine.length > maxChars) { dropped = i + 1; break; }
    entries.unshift(mine); used += mine.length + 2;
  }
  const head = dropped ? `(${dropped} earlier messages omitted)\n\n` : '';
  return `<conversation_so_far>\nThis conversation started before this session. Earlier messages, oldest first. "Tool use" and "Tool result" entries record tools you used through real tool calls: they are never text to write in a reply.\n\n${head}${entries.join('\n\n')}\n</conversation_so_far>`;
}

// Stateful, one per run. `result` messages are left to the loop.
class SdkMessageMapper {
  turn = 0; totalUsage = { inputTokens: 0, outputTokens: 0 }; lastAssistant; syntheticError;
  #current; #startedAt; #stopReason = 'end_turn'; #usage = { inputTokens: 0, outputTokens: 0 };
  constructor(o) { this.o = o; }
  unpairedToolCalls() {
    const last = this.o.messages.at(-1);
    if (last?.role !== 'assistant' || typeof last.content === 'string') return [];
    return last.content.filter((p) => p.type === 'tool_call');
  }
  handle(msg) {
    if (msg.parent_tool_use_id) return [];
    switch (msg.type) {
      case 'system': return this.#system(msg);
      case 'stream_event': return this.#stream(msg.event ?? {});
      case 'assistant': return this.#assistant(msg.message ?? {});
      case 'user': return this.#user(msg.message?.content);
      case 'rate_limit_event': this.o.onRateLimit?.(msg.rate_limit_info); return [];
      default: return [];
    }
  }
  #system(m) {
    if (m.subtype === 'init' && typeof m.session_id === 'string') this.o.onSessionId?.(m.session_id);
    else if (m.subtype === 'status' && m.status === 'requesting') this.#startedAt ??= Date.now();
    else if (m.subtype === 'api_retry') {
      const reason = m.error_status === 429 ? 'rate_limit' : m.error_status === 529 ? 'overloaded' : 'provider_error';
      return [{ type: 'retry', reason, attempt: num(m.attempt), maxAttempts: num(m.max_retries), delayMs: num(m.retry_delay_ms) }];
    }
    return [];
  }
  #stream(e) {
    switch (e.type) {
      case 'message_start': this.#startedAt ??= Date.now(); this.#stopReason = 'end_turn'; this.#usage = toUsage(e.message?.usage); return [];
      case 'content_block_delta': {
        const d = e.delta ?? {};
        if (d.type === 'text_delta') return [{ type: 'text_delta', text: String(d.text) }];
        if (d.type === 'thinking_delta') return [{ type: 'thinking_delta', text: String(d.thinking) }];
        if (d.type === 'input_json_delta') { const chars = String(d.partial_json ?? '').length; return chars ? [{ type: 'toolcall_delta', chars }] : []; }
        return [];
      }
      case 'message_delta': this.#stopReason = toStopReason(e.delta?.stop_reason); this.#usage = toUsage(e.usage); return [];
      case 'message_stop': {
        this.turn++;
        const completedAt = Date.now(), startedAt = this.#startedAt ?? completedAt, usage = this.#usage;
        addUsage(this.totalUsage, usage);
        this.#startedAt = undefined;
        const out = [{ type: 'turn_end', turn: this.turn, stopReason: this.#stopReason, usage, timing: { startedAt, completedAt, providerDurationMs: completedAt - startedAt } }];
        if (this.#stopReason === 'max_tokens' || this.#stopReason === 'refusal') out.push({ type: 'truncated', reason: this.#stopReason, continued: false });
        return out;
      }
      default: return [];
    }
  }
  #assistant(message) {
    const blocks = Array.isArray(message.content) ? message.content : [];
    if (message.model === SYNTHETIC_MODEL) {
      const text = blocks.map((b) => (b.type === 'text' ? String(b.text) : '')).join('');
      if (text) this.syntheticError = text;
      return [];
    }
    const id = String(message.id ?? '');
    if (!this.#current || this.#current.id !== id) {
      const fresh = { role: 'assistant', content: [] };
      this.#current = { id, message: fresh };
      this.o.messages.push(fresh);
      this.lastAssistant = fresh;
    }
    const events = [];
    for (const b of blocks) {
      const part = fromSdkBlock(b);
      this.#current.message.content.push(part);
      if (part.type === 'server_tool_call') events.push({ type: 'server_tool_call', id: part.id, name: part.name, input: part.input });
      else if (part.type === 'server_tool_result') events.push({ type: 'server_tool_result', toolUseId: part.toolUseId, resultType: part.resultType, data: part.data });
    }
    return events;
  }
  #user(raw) {
    if (!Array.isArray(raw)) return [];
    const blocks = raw.filter((b) => b.type === 'tool_result');
    if (!blocks.length) return [];
    const calls = new Map(this.unpairedToolCalls().map((c) => [c.id, c]));
    const events = [];
    const results = blocks.map((b) => {
      const id = String(b.tool_use_id);
      const bridged = this.o.takeResult(id);
      if (bridged) return bridged;
      const result = fromSdkToolResult(b), call = calls.get(id);
      events.push(
        { type: 'tool_call_start', toolCallId: id, name: call?.name ?? 'tool', args: call?.args ?? {} },
        { type: 'tool_call_end', toolCallId: id, result: resultText(result.content), isError: !!result.isError, durationMs: 0 },
      );
      return result;
    });
    this.o.messages.push({ role: 'tool', content: results });
    this.#current = undefined;
    events.push({ type: 'checkpoint', turn: this.turn });
    return events;
  }
  // After an abort: close dangling tool calls so the transcript stays paired.
  pairDanglingToolCalls(note) {
    const pending = this.unpairedToolCalls();
    if (!pending.length) return;
    this.o.messages.push({ role: 'tool', content: pending.map((c) => this.o.takeResult(c.id) ?? { type: 'tool_result', toolCallId: c.id, content: note, isError: true }) });
    this.#current = undefined;
  }
}

// ---------- tool bridge: our AgentTools as an in-process MCP server ----------
let redaction;
const redactOpts = () => (redaction ??= { secrets: environmentSecrets(process.env) });
function stripNulls(v) {
  if (Array.isArray(v)) return v.map(stripNulls);
  if (v !== null && typeof v === 'object') return Object.fromEntries(Object.entries(v).filter(([, x]) => x !== null).map(([k, x]) => [k, stripNulls(x)]));
  return v;
}
const preview = (c) => (typeof c === 'string' ? c : c.map((b) => (b.type === 'text' ? b.text : `[${b.type} ${b.mediaType}]`)).join('\n'));
const toMcpContent = (c) => (typeof c === 'string' ? (c ? [{ type: 'text', text: c }] : [])
  : c.map((b) => (b.type === 'text' ? { type: 'text', text: b.text } : b.type === 'image' ? { type: 'image', data: b.data, mimeType: b.mediaType } : { type: 'text', text: `[${b.mediaType} omitted: not supported here]` })));

export function createToolBridge({ tools, signal, maxToolResultChars, onEvent }) {
  const results = new Map(), invalidCounts = new Map();
  async function callTool(name, args, toolCallId, cancel) {
    const t0 = Date.now();
    onEvent({ type: 'tool_call_start', toolCallId, name, args });
    let content, details, isError = false, invalidArgAttempt;
    const tool = tools.find((t) => t.name === name);
    if (!tool) { content = unknownToolMessage(name, tools.map((t) => t.name)); isError = true; }
    else {
      try {
        let parsed;
        try { parsed = tool.parameters.parse(args); } catch (first) {
          if (tool.rawInputSchema) throw first;
          try { parsed = tool.parameters.parse(stripNulls(args)); } catch { throw first; }
        }
        const sig = typeof signal === 'function' ? signal() : signal;
        const signals = [cancel, AbortSignal.timeout(tool.timeoutMs ?? DEFAULT_TOOL_TIMEOUT_MS), ...(sig ? [sig] : [])];
        const raw = await tool.execute(parsed, { signal: AbortSignal.any(signals), toolCallId, onUpdate: (update) => onEvent({ type: 'tool_call_update', toolCallId, update }) });
        const out = typeof raw === 'string' ? { content: raw } : raw;
        content = out.content; details = out.details;
        for (const k of invalidCounts.keys()) if (k.startsWith(`${name}:`)) invalidCounts.delete(k);
      } catch (err) {
        isError = true;
        if (err instanceof ZodError) {
          const pretty = prettifyError(err), key = `${name}:${pretty}`;
          invalidArgAttempt = (invalidCounts.get(key) ?? 0) + 1;
          invalidCounts.set(key, invalidArgAttempt);
          const hints = argumentHints(tool, args);
          content = `Invalid arguments for tool \`${name}\`:\n${pretty}${hints ? `\n${hints}` : ''}\nRe-issue the call with each field as the correct type.`;
        } else content = err instanceof Error ? err.message : String(err);
      }
    }
    content = redactValue(content, redactOpts()); details = redactValue(details, redactOpts());
    onEvent({ type: 'tool_call_end', toolCallId, result: preview(content), details, isError, durationMs: Date.now() - t0, ...(invalidArgAttempt === undefined ? {} : { invalidArgAttempt }) });
    const result = { type: 'tool_result', toolCallId, content, isError };
    capToolResults([result], maxToolResultChars);
    return result;
  }

  let callCount = 0;
  const instance = {
    async connect(transport) {
      const inFlight = new Map();
      const reply = (id, body) => void transport.send({ jsonrpc: '2.0', id, ...body }).catch(() => {});
      transport.onclose = () => { for (const c of inFlight.values()) c.abort(); inFlight.clear(); };
      transport.onmessage = (message) => {
        const { id, method, params = {} } = message;
        if (method === 'notifications/cancelled') return void inFlight.get(params.requestId)?.abort();
        if (id === undefined || id === null || !method) return;
        if (method === 'initialize') {
          return reply(id, { result: { protocolVersion: params.protocolVersion ?? '2025-11-25', capabilities: { tools: { listChanged: true } }, serverInfo: { name: MCP_SERVER, version: '1.0.0' } } });
        }
        if (method === 'ping') return reply(id, { result: {} });
        if (method === 'tools/list') {
          const list = [];
          for (const tool of tools) {
            try { list.push({ name: tool.name, description: tool.description, inputSchema: resolveToolSchema(tool), _meta: { 'anthropic/alwaysLoad': true } }); } catch {} // unconvertible schema: left out
          }
          return reply(id, { result: { tools: list } });
        }
        if (method === 'tools/call') {
          const controller = new AbortController();
          inFlight.set(id, controller);
          const metaId = params._meta?.['claudecode/toolUseId'];
          const toolCallId = typeof metaId === 'string' ? metaId : `gg_call_${++callCount}`;
          void callTool(String(params.name), params.arguments ?? {}, toolCallId, controller.signal)
            .then((result) => { results.set(toolCallId, result); reply(id, { result: { content: toMcpContent(result.content), ...(result.isError ? { isError: true } : {}) } }); })
            .finally(() => inFlight.delete(id));
          return;
        }
        reply(id, { error: { code: -32601, message: `Method not found: ${method}` } });
      };
      await transport.start?.();
    },
  };
  const longest = Math.max(DEFAULT_TOOL_TIMEOUT_MS, ...tools.map((t) => t.timeoutMs ?? 0));
  return {
    config: { type: 'sdk', name: MCP_SERVER, instance, timeout: longest + SERVER_TIMEOUT_MARGIN_MS },
    allowedTools: () => tools.map((t) => TOOL_PREFIX + t.name),
    takeResult(id) { const r = results.get(id); results.delete(id); return r; },
  };
}

// ---------- loop ----------
class Channel {
  #items = []; #waiter; #closed = false;
  push(x) { if (this.#closed) return; this.#items.push(x); this.#waiter?.(); }
  end() { this.#closed = true; this.#waiter?.(); }
  async *[Symbol.asyncIterator]() {
    for (;;) {
      while (this.#items.length) yield this.#items.shift();
      if (this.#closed) return;
      await new Promise((r) => (this.#waiter = r));
      this.#waiter = undefined;
    }
  }
}
const isAbortLike = (err) => /abort/i.test(err instanceof Error ? err.message : String(err)) || err?.name === 'AbortError';

// ---------- warm spare ----------
// Starting Claude Code costs ~0.7 s before the model is even asked (process, config, MCP handshake). A spare is
// started ahead of the next request with the same options and taken by it; anything that differs (model, system
// prompt, tools, session to resume) starts cold as before. At most one, let go after SPARE_IDLE_MS unused.
let spare = null;
const sha = (x) => crypto.createHash('sha256').update(typeof x === 'string' ? x : JSON.stringify(x)).digest('hex').slice(0, 16);
const spareKey = (o, resume) => sha([o.executable, o.cwd, o.model, sha(o.system ?? ''), (o.tools ?? []).map((t) => t.name), o.maxTurns ?? 0, o.thinking ?? null, resume ?? null, sha(o.env ?? {})]);
export function closeSpare() {
  const s = spare; spare = null;
  if (!s) return;
  clearTimeout(s.timer);
  s.warm.then((w) => w?.close()).catch(() => {});
}
// Everything a query is started with; `link` carries what's only known once a request takes it.
function sdkOptions(o, resume, link) {
  const bridge = createToolBridge({ tools: o.tools ?? [], signal: () => link.signal, onEvent: (event) => link.onEvent?.(event) });
  const abortController = new AbortController();
  const effort = effortForThinking(o.thinking);
  const options = {
    cwd: o.cwd,
    model: o.model,
    systemPrompt: { type: 'custom', prompt: o.system ?? '' },
    tools: [], // none of Claude Code's own tools: only ours, through the bridge (and its safety checks)
    settingSources: [],
    strictMcpConfig: true,
    mcpServers: { [MCP_SERVER]: bridge.config },
    allowedTools: bridge.allowedTools(),
    canUseTool: async (name, toolInput) => (name.startsWith(TOOL_PREFIX) ? { behavior: 'allow', updatedInput: toolInput } : { behavior: 'deny', message: `${name} is not available in Roamies.` }),
    includePartialMessages: true,
    pathToClaudeCodeExecutable: o.executable,
    env: o.env,
    abortController,
    stderr: (d) => link.stderr?.(d),
    ...(resume ? { resume } : {}),
    ...(o.maxTurns ? { maxTurns: o.maxTurns } : {}),
    ...(effort ? { thinking: { type: 'adaptive' }, effort } : { thinking: { type: 'disabled' } }),
  };
  return { bridge, abortController, options };
}
/** Starts a spare for a next request with these options, unless one is already waiting for exactly that. */
export function warmSpare(o, resume) {
  const key = spareKey(o, resume);
  if (spare?.key === key) { clearTimeout(spare.timer); spare.timer = setTimeout(closeSpare, SPARE_IDLE_MS); spare.timer.unref?.(); return; }
  closeSpare();
  const link = {}, made = sdkOptions(o, resume, link);
  const warm = Promise.resolve().then(() => (o.startup ?? sdkStartup)({ options: made.options })).catch(() => null);
  spare = { key, link, ...made, warm, timer: setTimeout(closeSpare, SPARE_IDLE_MS) };
  spare.timer.unref?.();
}
function takeSpare(o, resume) {
  const s = spare;
  if (!s) return null;
  spare = null; clearTimeout(s.timer);
  if (s.key === spareKey(o, resume)) return s;
  s.warm.then((w) => w?.close()).catch(() => {});
  return null;
}

// One run: `messages` ends with the pending user turn(s); assistant and tool messages are appended to it.
async function* claudeCodeLoop(messages, o) {
  const channel = new Channel();
  let aborted = !!o.signal?.aborted, stderrTail = '';
  let start = messages.length;
  while (start > 0 && messages[start - 1].role === 'user') start--;
  const prior = messages.slice(0, start);
  const first = messages.slice(start).flatMap((m) => toSdkUserContent(m.content));
  if (!o.resume && prior.some((m) => m.role !== 'system')) first.unshift({ type: 'text', text: renderTranscript(prior, MAX_SEED_CHARS) });
  if (!first.length) first.push({ type: 'text', text: 'Continue.' });

  // A warm spare if one was started for exactly this; else (or if it turns out dead) a fresh process.
  const link = { signal: o.signal, onEvent: (event) => channel.push({ kind: 'event', event }), stderr: (d) => { stderrTail = (stderrTail + d).slice(-STDERR_TAIL_CHARS); } };
  let run, input, q, warmUsed = false;
  const begin = async (s) => {
    const fresh = {};
    run = s ?? { link: fresh, ...sdkOptions(o, o.resume, fresh) };
    Object.assign(run.link, link);
    input = new Channel();
    input.push(toSdkUserMessage(first));
    const w = s ? await s.warm : null;
    warmUsed = !!w;
    q = w ? w.query(input) : (o.query ?? sdkQuery)({ prompt: input, options: run.options });
    if (aborted) { input.end(); q.interrupt().catch(() => {}); }
    let seen = false;
    try { for await (const message of q) { seen = true; channel.push({ kind: 'sdk', message }); } channel.push({ kind: 'end' }); }
    catch (error) {
      if (warmUsed && !seen && !aborted) { try { q.close(); } catch {} return begin(null); } // the spare died while parked
      channel.push({ kind: 'error', error });
    }
  };
  const mapper = new SdkMessageMapper({ messages, takeResult: (id) => run.bridge.takeResult(id), onSessionId: o.onSessionId, onRateLimit: o.onRateLimit });
  void begin(o.keepWarm ? takeSpare(o, o.resume) : null);

  let killTimer;
  const onAbort = () => {
    aborted = true;
    input?.end();
    q?.interrupt().catch(() => {});
    killTimer = setTimeout(() => run?.abortController.abort(), INTERRUPT_GRACE_MS);
    channel.push({ kind: 'wake' });
  };
  if (aborted) onAbort(); else o.signal?.addEventListener('abort', onAbort, { once: true });

  let hitMaxTurns = false;
  try {
    for await (const item of channel) {
      if (item.kind === 'wake') continue;
      if (item.kind === 'event') { if (!aborted) yield item.event; continue; }
      if (item.kind === 'end') break;
      if (item.kind === 'error') {
        if (aborted || isAbortLike(item.error)) break;
        throw item.error instanceof Error ? item.error : new Error(String(item.error));
      }
      const message = item.message;
      // After Stop, keep the transcript in step until Claude Code reports the interrupted result, but stop emitting.
      if (aborted) { if (message.type === 'result') break; mapper.handle(message); continue; }
      if (message.type !== 'result') { yield* mapper.handle(message); continue; }
      if (message.subtype === 'error_max_turns') { hitMaxTurns = true; break; }
      if (message.subtype !== 'success') {
        const detail = (message.errors ?? []).join('\n') || mapper.syntheticError || stderrTail.trim();
        throw new Error(detail || `Claude Code stopped: ${message.subtype}`);
      }
      if (message.is_error) throw new Error(message.result || mapper.syntheticError || 'Claude Code request failed.');
      break;
    }
  } finally {
    o.signal?.removeEventListener('abort', onAbort);
    input?.end(); channel.end();
    if (killTimer) clearTimeout(killTimer);
    q?.close();
  }
  if (aborted) mapper.pairDanglingToolCalls(INTERRUPTED_NOTE);
  if (hitMaxTurns) yield { type: 'max_turns', totalTurns: mapper.turn, maxTurns: o.maxTurns ?? 0 };
  yield { type: 'agent_done', totalTurns: mapper.turn, totalUsage: { ...mapper.totalUsage } };
  return { message: mapper.lastAssistant ?? { role: 'assistant', content: [] }, totalTurns: mapper.turn, totalUsage: { ...mapper.totalUsage } };
}

// ---------- agent ----------
// Which message Claude Code saw last, as words and tool ids only: saving a session drops old images, and the
// fingerprint must still match after the agent is rebuilt from disk.
export function fingerprint(m) {
  const parts = typeof m?.content === 'string' ? [m.content] : (m?.content ?? []).map((p) => p.text ?? p.id ?? p.toolCallId ?? p.type);
  return crypto.createHash('sha256').update(JSON.stringify([m?.role, parts])).digest('hex').slice(0, 32);
}
// The Claude Code session to continue, or undefined when our history no longer matches it (trimmed, rewritten,
// or turns from another provider), so a fresh one is seeded with the transcript instead.
export function resumeId(state, messages) {
  if (!state?.count) return undefined;
  const history = messages.filter((m) => m.role !== 'system');
  if (history.length < state.count || fingerprint(history[state.count - 1]) !== state.anchor) return undefined;
  return history.slice(state.count).every((m) => m.role === 'user') ? state.sessionId : undefined;
}

/**
 * prompt(content) → AgentStream, getMessages(), setSignal(). Options: model, system, priorMessages, tools, maxTurns,
 * thinking, signal, transformContext, executable, cwd, env, resume ({ sessionId, count, anchor } or null),
 * onResume(state), onRateLimit, query (test seam).
 */
export class ClaudeCodeAgent {
  #running = false;
  constructor(options) {
    this.options = options;
    this.resume = options.resume ?? null;
    this.messages = [...(options.system ? [{ role: 'system', content: options.system }] : []), ...(options.priorMessages ?? [])];
  }
  getMessages() { return [...this.messages]; }
  /** keepWarm only: start a Claude Code process now for the next prompt, so it doesn't wait for one to start. */
  warm() {
    if (!this.options.keepWarm || this.#running) return;
    warmSpare(this.options, resumeId(this.resume, [...this.messages, { role: 'user', content: '' }]));
  }
  setSignal(signal) { this.options = { ...this.options, signal }; }
  prompt(content) {
    if (this.#running) throw new Error('Agent is already running');
    this.#running = true;
    this.messages.push({ role: 'user', content });
    return new AgentStream(this.#run(), () => { this.#running = false; });
  }
  async *#run() {
    const o = this.options;
    const resume = resumeId(this.resume, this.messages);
    // The live block and trimming apply to what's sent, not to the saved history.
    const loopMsgs = [...(o.transformContext ? await o.transformContext(this.messages, {}) : this.messages)];
    const base = loopMsgs.length;
    let sessionId = resume;
    try {
      return yield* claudeCodeLoop(loopMsgs, {
        ...o, resume,
        onSessionId: (id) => { sessionId = id; },
        onRateLimit: o.onRateLimit ?? ((info) => void recordClaudeCodeRateLimit(info).catch(() => {})),
      });
    } finally {
      this.messages.push(...loopMsgs.slice(base));
      if (sessionId) {
        const history = this.messages.filter((m) => m.role !== 'system');
        this.resume = { sessionId, count: history.length, anchor: fingerprint(history.at(-1)) };
        o.onResume?.(this.resume);
      }
      if (o.keepWarm) warmSpare(o, resumeId(this.resume, [...this.messages, { role: 'user', content: '' }]));
    }
  }
}
