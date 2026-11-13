// Speaks replies with Fish Audio, Gemini or VoiceStudio (the user's pick) and hands the audio to the overlay
// ({ kind: 'speech' } messages).
// - VoiceStudio is the VoiceStudio app on this computer (voicestudio.mjs): no key, its selected engine, the user's saved
//   VoiceStudio voice. A reply that fails there is reported and stays text; it never falls back to a paid service.
//   Beats aren't made in it (each one would hold up the GPU for seconds), so its replies have none.
// - Fish Audio: the model is the one the user picked (Drama 3 or S2.1 Pro) while the account has credit, the free tier
//   otherwise (checked when the key is set, at start and after a failure). If a reply's voice fails before any
//   audio, it's retried on the next model down from the pick, never a pricier one (Drama 3 → S2.1 Pro → free;
//   S2.1 Pro → free) with everything said so far, and one that works is kept from then on; if it fails after
//   audio, the rest stays text only.
// - Each reply has its own id, so audio from one that was stopped can never play over the next.
// - Beats: when something slow starts (a web search, a page, the screen) and the model hasn't said anything,
//   a short line ("One sec.", "On it." and 32 more, picked at random) plays so it's never silent. They're made once
//   in the voice and kept, so they play instantly.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DEFAULT_VOICE, FISH_MODELS, FREE_MODEL, FishVoice, RATE, fishAccount } from './fish.mjs';
import { StudioVoice, studioStatus } from './voicestudio.mjs';
import { GEMINI_DEFAULT_VOICE, GEMINI_MODEL, GEMINI_MODELS, GEMINI_PITCH, GEMINI_RATE, GeminiVoice, geminiAccount } from './gemini.mjs';

// The services that can speak. models: priciest first, the pick and the ones after it are tried in turn; pick:
// the one used until the user picks (else the first); freeModel: the one for an account with no credit; rate:
// the PCM's sample rate; warmDelay: how long a key, voice or model must stay put before its beats are made
// (Gemini has 30 voices to try out, and making 34 beats for each one passed over would waste the allowance).
export const PROVIDERS = {
  fish: { id: 'fish', name: 'Fish Audio', Voice: FishVoice, account: fishAccount, models: FISH_MODELS, freeModel: FREE_MODEL, rate: RATE, defaultVoice: DEFAULT_VOICE },
  gemini: { id: 'gemini', name: 'Gemini', Voice: GeminiVoice, account: geminiAccount, models: GEMINI_MODELS, pick: GEMINI_MODEL, freeModel: null, rate: GEMINI_RATE, play: Math.round(GEMINI_RATE * GEMINI_PITCH), defaultVoice: GEMINI_DEFAULT_VOICE, warmDelay: 15_000 },
};

// Silence trimmed from both ends (a little kept), so a beat starts the moment it plays.
export function trimSilence(pcm, rate) {
  const s = new Int16Array(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + (pcm.byteLength & ~1))), loud = 500, pad = Math.round(rate * 0.04);
  let a = 0, b = s.length - 1;
  while (a < s.length && Math.abs(s[a]) < loud) a++;
  if (a === s.length) return Buffer.from(s.buffer); // nothing loud in it: left as it is
  while (b > a && Math.abs(s[b]) < loud) b--;
  return Buffer.from(s.slice(Math.max(0, a - pad), Math.min(s.length, b + 1 + pad)).buffer);
}

// One or two words each, so a beat never sounds canned; any of them must fit before a search, a page, the screen or a drawing.
export const BEATS = [
  'One sec.', 'On it.', 'Hang on.', 'Hold on.', 'Got it.', 'Okay.', 'Sure.', 'Right.', 'Alright.', 'Gotcha.',
  'Checking.', "Let's see.", 'Sure thing.', 'Coming up.', 'Sec.', 'One moment.', 'Bear with.', 'Hmm, okay.', 'Got you.', 'Right, sec.',
  'Sure, sec.', 'Okay, sec.', 'Roger.', 'Yep.', 'Yep, sec.', 'Two secs.', 'Hold tight.', 'Mm-hmm.', 'Will do.', 'Righto.',
  'Moment.', 'Okay, checking.', 'Sure, checking.', 'Okie-dokie.',
];
const RECENT = 8; // a beat isn't picked again until this many others have played

// The beats shipped with the app (beats/make.mjs), next to this file or the bundle: (voice, model) → Map of phrase →
// { rate, pcm } (base64), or null for a voice or model not recorded. The free tier uses the S2.1 Pro takes.
export function bundledBeats(dir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'beats')) {
  let index = null;
  try { index = JSON.parse(fs.readFileSync(path.join(dir, 'index.json'), 'utf8')); } catch { return () => null; }
  const sets = new Map();
  return (voice, model) => {
    const k = `${voice}:${model === FREE_MODEL ? 's2.1-pro' : model}`, set = index.sets?.[k];
    if (!set) return null;
    if (!sets.has(k)) {
      let all = null;
      try { all = fs.readFileSync(path.join(dir, set.file)); } catch {}
      sets.set(k, all && new Map(Object.entries(set.beats).map(([phrase, [at, n]]) => [phrase, { rate: index.rate, pcm: all.subarray(at, at + n).toString('base64') }])));
    }
    return sets.get(k);
  };
}

// beatStore: { read(name), write(name, data) }, where beats made here are kept between runs (none: made again each run).
export function createSpeaker({ emit, onProblem = () => {}, log = () => {}, FishVoiceImpl = FishVoice, account = fishAccount, providers = {}, beatPhrases = BEATS, bundled = bundledBeats(), beatStore = null, LocalVoiceImpl = StudioVoice, localStatus = studioStatus }) {
  const all = { ...PROVIDERS, fish: { ...PROVIDERS.fish, Voice: FishVoiceImpl, account }, ...providers };
  let P = all.fish; // the service in use
  let key = null, voice = P.defaultVoice, pref = P.pick ?? P.models[0], model = pref, seq = 0, current = null, checking = null;
  // The pick and the cheaper ones after it (models run from priciest to free).
  const chain = () => P.models.slice(Math.max(0, P.models.indexOf(pref)));
  let beats = new Map(), recent = [], warming = null; // phrase → [base64 PCM], for the current key, voice and model
  // A random beat, not one of the last few: one that's already made if there is one, so it plays instantly.
  const shipped = () => bundled(voice, model);
  const ready = (p) => beats.has(p) || !!shipped()?.has(p);
  const pickBeat = () => {
    const fresh = beatPhrases.filter((p) => !recent.includes(p));
    const pool = [fresh.filter(ready), beatPhrases.filter(ready), fresh, beatPhrases].find((x) => x.length);
    const phrase = pool?.[Math.floor(Math.random() * pool.length)] ?? BEATS[0];
    recent = [...recent, phrase].slice(-Math.min(RECENT, Math.max(0, beatPhrases.length - 1)));
    return phrase;
  };

  // Make the beats once, one at a time (well under the services' concurrency limits), and keep them for next time.
  // Failures just leave a gap: that beat is then spoken live instead. Three in a row and it stops for now (no
  // credit, or too many requests): the next account check starts it again.
  function warm() {
    if (warming) return warming;
    const k = key, vo = voice, mo = model, p = P;
    const same = () => key === k && voice === vo && model === mo && P === p;
    const name = `${p.id}.${vo}.${mo}`.replace(/[^\w.-]/g, '_');
    const mine = warming = (async () => {
      if (p.warmDelay) await new Promise((r) => { setTimeout(r, p.warmDelay).unref?.(); });
      if (!same()) return;
      const kept = await Promise.resolve().then(() => beatStore?.read(name)).catch(() => null);
      if (!same()) return;
      for (const [phrase, pcm] of Object.entries(kept?.beats ?? {})) if (beatPhrases.includes(phrase) && typeof pcm === 'string' && !beats.has(phrase)) beats.set(phrase, [pcm]);
      let made = 0, failed = 0;
      for (const phrase of beatPhrases) {
        if (!same() || failed >= 3) break;
        if (beats.has(phrase) || bundled(vo, mo)?.has(phrase)) continue; // kept, or shipped with the app
        const chunks = await new Promise((done) => {
          const got = [];
          const v = new p.Voice({ key: k, voice: vo, model: mo, onAudio: (pcm) => got.push(Buffer.from(pcm.buffer, pcm.byteOffset, pcm.byteLength)), onEnd: () => done(got), onError: () => done(null) }).open();
          v.say(phrase); v.end();
        });
        if (!chunks?.length) { failed++; continue; }
        failed = 0;
        if (same()) { beats.set(phrase, [trimSilence(Buffer.concat(chunks), p.rate).toString('base64')]); made++; }
      }
      if (made && same()) await Promise.resolve().then(() => beatStore?.write(name, { rate: p.rate, beats: Object.fromEntries([...beats].map(([phrase, c]) => [phrase, c.join('')])) })).catch(() => {});
    })().finally(() => {
      if (warming !== mine) return; // the service was changed: its own key starts this again
      warming = null;
      if (key && !same()) warm(); // the key, voice or model changed meanwhile: make the new one's
    });
    return mine;
  }

  // Returns the account check: { ok, credit } or { ok: false, reason: 'key' | 'network' }.
  async function check() {
    if (!key) return { ok: false, reason: 'key' };
    if (checking) return checking;
    const p = P;
    const mine = checking = p.account(key).then((a) => {
      if (P !== p) return a; // the service was changed while this was out: its answer is about the old one
      // No credit: the free tier. Credit again: back to the best model, else keep the paid one that worked.
      if (a.ok) model = a.credit != null && a.credit <= 0.01 && p.freeModel ? p.freeModel : model === p.freeModel ? pref : model;
      log(`${p.id}_account`, { ok: a.ok, reason: a.reason, model });
      if (a.ok) warm();
      return a;
    }).finally(() => { if (checking === mine) checking = null; });
    return mine;
  }

  function stopAll() {
    if (current) { current.abort(); current = null; }
    emit({ kind: 'speech', stop: true });
  }

  // VoiceStudio: on while picked (it needs no key); local is its last check ({ ok, reason, message, engine, voices, … }).
  let localOn = false, localVoice = null, local = null;
  // Is VoiceStudio up, with a usable engine selected? Never throws; the answer is kept for the panel.
  async function checkLocal() {
    const s = await localStatus().catch((e) => ({ ok: false, reason: 'offline', message: e.message }));
    local = s;
    log('voicestudio_status', { ok: s.ok, reason: s.reason, engine: s.engine });
    return s;
  }
  // A reply in VoiceStudio: its socket opens now, one request per sentence, played as each streams in.
  function startLocal() {
    stopAll();
    const id = `s${++seq}`, said = [], t0 = Date.now();
    let heard = false, done = false, v = null;
    const finish = (whole = false) => {
      if (done) return;
      done = true;
      const chars = whole && heard ? said.join(' ').replace(/\[[^\]]*\]\s*/g, '').length : 0;
      emit({ kind: 'speech', id, end: true, ...(chars ? { chars } : {}) });
      if (current === me) current = null;
    };
    const me = {
      id,
      say(s) { if (done) return; said.push(s); v.say(s); },
      beat() { return false; },
      end() { if (!done) v.end(); },
      abort() { if (done) return; done = true; v?.abort(); if (current === me) current = null; },
    };
    v = new LocalVoiceImpl({
      voice: localVoice,
      onAudio: (pcm, rate) => {
        if (done) return;
        if (!heard) { heard = true; log('speech_first_audio', { id, provider: 'voicestudio', ms: Date.now() - t0 }); if (local && !local.ok) checkLocal(); } // working again: refresh the panel's status
        emit({ kind: 'speech', id, rate, pcm: Buffer.from(pcm.buffer, pcm.byteOffset, pcm.byteLength).toString('base64') });
      },
      onEnd: () => finish(true),
      onError: (err) => {
        if (done) return;
        log('speech_error', { id, provider: 'voicestudio', heard: !!err.heard, reason: err.reason, message: err.message });
        if (err.reason) local = { ...(local ?? {}), ok: false, reason: err.reason, message: err.message };
        if (!heard) onProblem({ provider: 'voicestudio', reason: err.reason ?? 'error', message: err.message });
        finish();
      },
    });
    log('speech_start', { id, provider: 'voicestudio', voice: localVoice });
    v.open?.(); // opening costs a moment: do it while the model thinks
    current = me;
    return me;
  }

  /** A new reply's voice, its socket opening now. say(sentence) as they come, then end(). */
  function start() {
    if (localOn) return startLocal();
    if (!key) return null;
    stopAll();
    const id = `s${++seq}`, said = [];
    let ended = false, tries = 0, v = null, heard = false, done = false, beat = false, t0 = Date.now();
    const me = {
      id,
      say(s) { if (done) return; said.push(s); v.say(s); },
      /** Something slow is starting: a short beat, unless it's already said something. */
      beat() {
        if (done || beat || said.length || heard) return false;
        beat = true;
        const phrase = pickBeat(), shippedBeat = shipped()?.get(phrase), chunks = beats.get(phrase);
        if (!shippedBeat && !chunks) { me.say(phrase); return true; }
        log('speech_beat', { id, ms: Date.now() - t0 });
        if (shippedBeat) emit({ kind: 'speech', id, rate: shippedBeat.rate, pcm: shippedBeat.pcm });
        else for (const pcm of chunks) emit({ kind: 'speech', id, rate: P.play ?? P.rate, pcm });
        return true;
      },
      end() { if (done || ended) return; ended = true; v.end(); },
      abort() { if (done) return; done = true; v?.abort(); if (current === me) current = null; },
    };
    // A reply that played to its end also says how much it said (without the voice cues), so the overlay can learn
    // how fast the voice talks and pace drawings to it.
    const finish = (whole = false) => {
      if (done) return;
      done = true;
      const chars = whole && heard ? said.join(' ').replace(/\[[^\]]*\]\s*/g, '').length : 0;
      emit({ kind: 'speech', id, end: true, ...(chars ? { chars } : {}) });
      if (current === me) current = null;
    };
    const connect = (m) => {
      tries++;
      v = new P.Voice({
        key, voice, model: m,
        onAudio: (pcm) => {
          if (done) return;
          if (!heard) { heard = true; if (m !== model) model = m; log('speech_first_audio', { id, model: m, ms: Date.now() - t0 }); }
          emit({ kind: 'speech', id, rate: P.play ?? P.rate, pcm: Buffer.from(pcm.buffer, pcm.byteOffset, pcm.byteLength).toString('base64') });
        },
        onEnd: () => finish(true),
        onError: async (err) => {
          if (done) return;
          log('speech_error', { id, model: m, heard: err.heard, message: err.message });
          if (!err.heard && !heard) {
            // Is it the key? Asked whether or not there's another model to try (Gemini has just the one).
            const a = await check();
            if (done) return;
            if (!a.ok && a.reason === 'key') { onProblem('key'); return finish(); }
            if (tries < chain().length) {
              // Retry: the free tier when there's no credit, else the next model down (the free one again once at the bottom).
              const c = chain();
              const next = a.ok && P.freeModel && model === P.freeModel ? P.freeModel : c[Math.min(c.indexOf(m) + 1, c.length - 1)];
              connect(next);
              for (const s of said) v.say(s);
              if (ended) v.end();
              return;
            }
            onProblem(err.message);
          }
          finish();
        },
      }).open();
    };
    log('speech_start', { id, model });
    connect(model);
    current = me;
    return me;
  }

  return {
    setKey(k) { if (localOn) return; if ((k || null) !== key) { beats = new Map(); if (!k) stopAll(); } key = k || null; if (key) check(); },
    // The user's pick. Beats are remade in it; with no credit it stays on the free tier until there is some.
    setModel(m) {
      if (!P.models.includes(m) || m === P.freeModel || m === pref) return;
      pref = m; beats = new Map();
      if (model !== P.freeModel) model = m;
      if (key) warm();
    },
    get pick() { return pref; },
    setVoice(v) { if ((v || P.defaultVoice) !== voice) beats = new Map(); voice = v || P.defaultVoice; if (key) warm(); },
    // Fish Audio, Gemini or VoiceStudio. Starts clean: the key, voice and model are the old service's, so set them again after.
    setProvider(id) {
      const p = all[id];
      if (id === 'voicestudio') {
        if (localOn) return;
        if (key) stopAll();
        localOn = true; key = null; checking = null; warming = null; beats = new Map();
        checkLocal();
        return;
      }
      if (!p || (p === P && !localOn)) return;
      if (key || localOn) stopAll(); // nothing can be playing without a key or VoiceStudio (and nothing is sent when the app is just starting)
      localOn = false; P = p; key = null; checking = null; warming = null; beats = new Map();
      voice = p.defaultVoice; pref = p.pick ?? p.models[0]; model = pref;
    },
    get provider() { return localOn ? 'voicestudio' : P.id; },
    // The VoiceStudio voice (a saved profile id; null: the engine's default voice).
    setLocalVoice(v) { localVoice = typeof v === 'string' && v ? v : null; },
    get localVoice() { return localVoice; },
    get local() { return local; },
    checkLocal,
    get ready() { return beats.size + beatPhrases.filter((p) => !beats.has(p) && shipped()?.has(p)).length; },
    get model() { return model; },
    get on() { return localOn || !!key; },
    check,
    start,
    stop: stopAll,
  };
}
