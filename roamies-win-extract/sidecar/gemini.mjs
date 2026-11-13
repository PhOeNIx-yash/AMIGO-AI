// Spoken replies through Gemini's text-to-speech (the Interactions API, with the user's own AI Studio key).
// There's no live socket: each sentence is its own streaming request, started the moment it's said (first audio
// in ~0.8-1.1 s, measured Nov 2026), a few at a time. Audio is handed on strictly in the order the sentences were
// said: the first one plays as it streams, the ones behind it are held until it's their turn. Raw 16-bit mono
// PCM at 24 kHz comes back as base64 in server-sent events. Same shape as FishVoice, so the speaker can use either.
import { CUE, VOICE_CUES } from './speech.mjs';

export const GEMINI_API = 'https://generativelanguage.googleapis.com/v1beta';
// Priciest first, like Fish's: a reply that fails on Flash before any audio is tried again on Flash Lite.
export const GEMINI_MODELS = ['gemini-3.8-flash-tts', 'gemini-3.8-flash-lite-tts'];
// The one used unless the user picks the other: the fast, cheap one.
export const GEMINI_MODEL = 'gemini-3.8-flash-lite-tts';
// The two the user picks between (Settings > Voice in the panel). per10s: USD for ten seconds of speech, from
// Google's pricing page (Nov 2026, the price through 2026; it doubles on Jan 1, 2027). Both have a free tier.
export const GEMINI_CHOICES = [
  { id: 'gemini-3.8-flash-lite-tts', name: 'Flash Lite', per10s: 0.0015 },
  { id: 'gemini-3.8-flash-tts', name: 'Flash', per10s: 0.00225 },
];
export const GEMINI_RATE = 24000;
// Played a little fast so the voice sits higher: 1.06 is about one semitone up (and 6% quicker).
// ponytail: tape-style speed-up, pitch and tempo move together; a real pitch shifter if tempo must stay.
export const GEMINI_PITCH = 1.06;
// Left alone Gemini talks slowly. This goes on every sentence's style, after its mood: about 20% quicker (measured).
export const GEMINI_PACE = 'speaking quickly';
export const GEMINI_DEFAULT_VOICE = 'Kore';
// Each look's own Gemini voice until the user picks another for it (as each has its own Fish Audio voice).
export const GEMINI_LOOK_VOICES = { classic: 'Puck', jarvis: 'Charon', anime: 'Leda', sarah: 'Sulafat', adrian: 'Achird', rita: 'Despina' };
// The prebuilt voices and how Google describes each (AI Studio speech generation docs, Nov 2026).
export const GEMINI_VOICES = [
  ['Zephyr', 'Bright'], ['Puck', 'Upbeat'], ['Charon', 'Informative'], ['Kore', 'Firm'], ['Fenrir', 'Excitable'], ['Leda', 'Youthful'],
  ['Orus', 'Firm'], ['Aoede', 'Breezy'], ['Callirrhoe', 'Easy-going'], ['Autonoe', 'Bright'], ['Enceladus', 'Breathy'], ['Iapetus', 'Clear'],
  ['Umbriel', 'Easy-going'], ['Algieba', 'Smooth'], ['Despina', 'Smooth'], ['Erinome', 'Clear'], ['Algenib', 'Gravelly'], ['Rasalgethi', 'Informative'],
  ['Laomedeia', 'Upbeat'], ['Achernar', 'Soft'], ['Alnilam', 'Firm'], ['Schedar', 'Even'], ['Gacrux', 'Mature'], ['Pulcherrima', 'Forward'],
  ['Achird', 'Friendly'], ['Zubenelgenubi', 'Casual'], ['Vindemiatrix', 'Gentle'], ['Sadachbia', 'Lively'], ['Sadaltager', 'Knowledgeable'], ['Sulafat', 'Warm'],
].map(([id, style]) => ({ id, style }));
// Sentences being made at once. Eight at a time went through fine, but the free tier's limits aren't published.
const MAX_IN_FLIGHT = 3;

// Gemini reads its text word for word, so the Fish voice cues ("[warm] Hello") must not be in it. They're taken
// out: the first one that's a mood or a tone becomes the sentence's style, and a sound or a pause that Gemini has an
// inline tag for becomes that tag ("[laughing] Ha" → "<laugh> Ha"; tags from Google's speech generation docs, Nov 2026).
const LEAD = /^\s*\[([a-z]+(?:[ ,-]+[a-z]+){0,3})\]\s*/; // a free description at the start ("[quietly proud]")
const NOT_STYLE = new Set([...VOICE_CUES.sounds, ...VOICE_CUES.effects, 'emphasis']);
export const GEMINI_TAGS = {
  laughing: 'laugh', chuckling: 'giggle', sobbing: 'sob', 'crying loudly': 'cry', sighing: 'sigh', groaning: 'groan',
  panting: 'pant', gasping: 'gasp', yawning: 'yawn', 'clear throat': 'throat-clearing', break: 'short pause', 'long-break': 'long pause',
};
export function plain(text) {
  let s = String(text ?? ''), style = null;
  const cue = (c) => {
    const tag = GEMINI_TAGS[c.toLowerCase()];
    if (tag) return `<${tag}> `;
    if (!style && !NOT_STYLE.has(c.toLowerCase())) style = c;
    return '';
  };
  const lead = s.match(LEAD);
  if (lead) s = cue(lead[1]) + s.slice(lead[0].length);
  s = s.replace(CUE, (m) => cue(m.slice(1, -1))).replace(/\s{2,}/g, ' ').trim();
  return { text: s, style };
}

export const speechRequest = (text, { voice = GEMINI_DEFAULT_VOICE, model = GEMINI_MODEL, style = null } = {}) => ({
  model,
  input: [{ type: 'user_input', content: [{ type: 'text', text, ...(style ? { annotations: [{ type: 'speech_metadata', style }] } : {}) }] }],
  response_format: { type: 'audio' },
  generation_config: { speech_config: [{ voice }] },
  stream: true,
});

/** Is the key good? Never throws. Same answer shape as fishAccount; Gemini has no credit to read. */
export async function geminiAccount(key, { fetchImpl = fetch, timeout = 6000 } = {}) {
  try {
    const r = await fetchImpl(`${GEMINI_API}/models?pageSize=1`, { headers: { 'x-goog-api-key': key }, signal: AbortSignal.timeout(timeout) });
    // A bad key is a 400 ("API_KEY_INVALID"), not a 401; this request has nothing else that could be invalid.
    if (r.status === 400 || r.status === 401 || r.status === 403) return { ok: false, reason: 'key' };
    if (!r.ok) return { ok: false, reason: 'network' };
    return { ok: true, credit: null };
  } catch {
    return { ok: false, reason: 'network' };
  }
}

/**
 * One reply's voice. say() as many times as needed, then end(). abort() stops at once, silently.
 * onAudio(Uint8Array) gets PCM (whole 16-bit samples); onEnd() once after the last audio; onError(err) instead
 * of onEnd when it fails (heard = whether any audio had arrived; status = the HTTP status, if there was one).
 * It never says a failure is the key's fault: a 400 is also what an unknown voice gets. The speaker checks.
 */
export class GeminiVoice {
  constructor({ key, voice = GEMINI_DEFAULT_VOICE, model = GEMINI_MODEL, fetchImpl = fetch, onAudio, onEnd, onError, audioTimeout = 8000, maxInFlight = MAX_IN_FLIGHT }) {
    Object.assign(this, { key, voice, model, fetchImpl, onAudio, onEnd, onError, audioTimeout, maxInFlight });
    this.jobs = []; // one per sentence, in the order said: { text, style, chunks, state: waiting → running → done }
    this.head = 0; // the sentence whose audio is being handed on now
    this.state = 'open'; // open → ending → done
    this.heard = false;
    this.stopper = new AbortController();
  }

  open() { return this; } // nothing to connect ahead of time

  say(text) {
    if (this.state !== 'open') return;
    const p = plain(text);
    if (!/[\p{L}\p{N}]/u.test(p.text.replace(/<[^>]*>/g, ''))) return; // nothing to say ("…", a lone cue or tag): asking would get no audio and fail the reply
    this.jobs.push({ ...p, chunks: [], state: 'waiting', odd: null, timer: null });
    this.pump();
  }

  end() {
    if (this.state !== 'open') return;
    this.state = 'ending';
    this.drain();
  }

  abort() { this.close(); }

  // Start the sentences that are waiting, up to the limit.
  pump() {
    let running = this.jobs.filter((j) => j.state === 'running').length;
    for (const j of this.jobs) {
      if (running >= this.maxInFlight) break;
      if (j.state === 'waiting') { running++; this.run(j); }
    }
  }

  async run(job) {
    job.state = 'running';
    const arm = () => { clearTimeout(job.timer); job.timer = setTimeout(() => this.fail(new Error('Gemini stopped sending audio')), this.audioTimeout); };
    arm();
    try {
      const r = await this.fetchImpl(`${GEMINI_API}/interactions`, {
        method: 'POST', headers: { 'x-goog-api-key': this.key, 'content-type': 'application/json' },
        body: JSON.stringify(speechRequest(job.text, { voice: this.voice, model: this.model, style: [job.style, GEMINI_PACE].filter(Boolean).join(', ') })), signal: this.stopper.signal,
      });
      if (!r.ok) {
        const said = (await r.text().catch(() => '')).match(/"message"\s*:\s*"((?:\\.|[^"\\])*)"/)?.[1];
        throw Object.assign(new Error(r.status === 429 ? 'Gemini is rate limiting' : `Gemini: ${said ?? `error ${r.status}`}`), { status: r.status });
      }
      const text = new TextDecoder();
      let rest = '', got = false;
      const line = (l) => {
        // Server-sent events: only the "data:" lines matter ("event:" repeats what's in them; "[DONE]" ends it).
        const s = l.replace(/^data:\s*/, '').trim();
        if (s[0] !== '{') return;
        let m;
        try { m = JSON.parse(s); } catch { return; }
        if (m.error) throw new Error(`Gemini: ${m.error.message ?? 'error'}`);
        if (m.delta?.type !== 'audio' || !m.delta.data) return;
        const pcm = this.whole(job, Buffer.from(m.delta.data, 'base64'));
        if (!pcm.length) return;
        got = true; arm();
        job.chunks.push(pcm);
        this.drain();
      };
      for await (const part of r.body) {
        if (this.state === 'done') return;
        const lines = (rest + text.decode(part, { stream: true })).split('\n');
        rest = lines.pop();
        for (const l of lines) line(l);
      }
      line(rest);
      if (!got) throw new Error('Gemini sent no audio');
    } catch (e) {
      return this.fail(e);
    }
    if (this.state === 'done') return;
    clearTimeout(job.timer);
    job.state = 'done';
    this.pump();
    this.drain();
  }

  // Hand on what's ready, in the order said; a sentence still streaming holds back the ones after it.
  drain() {
    while (this.state !== 'done' && this.head < this.jobs.length) {
      const j = this.jobs[this.head];
      for (const pcm of j.chunks.splice(0)) { this.heard = true; this.onAudio?.(pcm); }
      if (j.state !== 'done') return;
      this.head++;
    }
    if (this.state === 'ending') { this.close(); this.onEnd?.(); }
  }

  // PCM samples are 2 bytes; a chunk can end mid-sample, so carry the odd byte into the sentence's next one.
  whole(job, b) {
    if (job.odd != null) { b = Buffer.concat([Buffer.from([job.odd]), b]); job.odd = null; }
    if (b.length % 2) { job.odd = b[b.length - 1]; b = b.subarray(0, b.length - 1); }
    return b;
  }

  close() {
    if (this.state === 'done') return;
    this.state = 'done';
    for (const j of this.jobs) clearTimeout(j.timer);
    this.stopper.abort();
  }

  fail(err) {
    if (this.state === 'done') return;
    this.close();
    err.heard = this.heard;
    this.onError?.(err);
  }
}
