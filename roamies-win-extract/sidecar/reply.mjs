// Turns the agent's raw text stream into what the sidekick shows and does.
// - Emote tags like [hype] (anywhere, even split across chunks) become emote events and are removed.
// - A later turn that only repeats the previous turn's text (models do this after a tool call) is dropped.
// - Text from a new turn is separated from earlier text with a space.
export const EMOTES = ['hype', 'upset', 'remind', 'motivate', 'wave'];
const TAG = new RegExp(`\\[(${EMOTES.join('|')})\\]\\s*`, 'i');
const LONGEST = Math.max(...EMOTES.map((e) => e.length)) + 2;

const norm = (s) => s.replace(/\s+/g, ' ').trim().toLowerCase();

// A tool call written out as text ("[called read_window {…}]", "Tool use: …"): models copy it from a conversation
// handed over as a transcript. It's never something to show or say (in brackets the voice took it as a cue and read
// it out). Dropped from the stream, also when it comes split across chunks or mid-line.
const ECHO_STARTS = ['[called ', 'Tool use: ', 'Tool result: '];
const ECHO_LINE = /^[ \t]*(?:\[called [\w.-]+(?: [^\n]*)?\]|Tool (?:use|result): [^\n]*)[ \t]*$/;
export class EchoFilter {
  constructor() { this.buf = ''; }
  push(text) {
    this.buf += text;
    let out = '';
    for (;;) {
      const nl = this.buf.indexOf('\n');
      const line = nl < 0 ? this.buf : this.buf.slice(0, nl);
      // Where an echo might begin in this line: at its start for "Tool …", anywhere for "[called ".
      let at = this.#echoAt(line, nl < 0);
      // Spaces at the end of an unfinished line are held too: they belong to an echo if one follows.
      if (at < 0 && nl < 0) at = line.replace(/[ \t]+$/, '').length;
      if (at < 0) { out += line + '\n'; this.buf = this.buf.slice(nl + 1); continue; }
      const from = line.slice(0, at).replace(/[ \t]+$/, '').length; // the spaces before it go with it
      out += line.slice(0, from);
      if (nl < 0) { this.buf = line.slice(from); return out; } // held until the line ends
      const held = line.slice(from);
      if (!ECHO_LINE.test(held.slice(at - from))) out += held + '\n';
      else if (from) out += '\n';
      this.buf = this.buf.slice(nl + 1);
    }
  }
  /** At the end of a turn: what's held goes out, unless it's an echo. */
  flush() { const b = this.buf; this.buf = ''; return ECHO_LINE.test(b) ? '' : b; }
  #echoAt(line, open) {
    const i = line.indexOf('[called ');
    if (i >= 0) return i;
    const t = line.replace(/^[ \t]+/, '');
    if (ECHO_STARTS.some((s) => s !== '[called ' && t.startsWith(s))) return line.length - t.length;
    // An unfinished line that could still become one: hold its tail.
    if (open) for (const s of ECHO_STARTS) for (let k = Math.min(s.length - 1, line.length); k > 0; k--) {
      const tail = line.slice(line.length - k);
      if (s.startsWith(tail) && (s === '[called ' || !line.slice(0, line.length - k).trim())) return line.length - k;
    }
    return -1;
  }
}

export class ReplyStream {
  constructor(emit) {
    this.emit = emit; // ({ type: 'text', text } | { type: 'emote', action })
    this.pending = ''; // raw text that might still be the start of a tag
    this.turn = ''; // this turn's visible text so far
    this.held = ''; // visible text held back while it matches the previous turn
    this.prev = ''; // previous turn's visible text, normalised
    this.shown = false; // anything shown yet in this run
    this.started = false; // this turn has released text
  }

  push(delta) {
    this.pending += delta;
    for (;;) {
      const m = TAG.exec(this.pending);
      if (!m) break;
      this.visible(this.pending.slice(0, m.index));
      this.emit({ type: 'emote', action: m[1].toLowerCase() });
      this.pending = this.pending.slice(m.index + m[0].length);
    }
    // Hold back a trailing "[hyp" that could still become a tag.
    const open = this.pending.lastIndexOf('[');
    let keep = '';
    if (open >= 0 && this.pending.length - open <= LONGEST) {
      const tail = this.pending.slice(open + 1).toLowerCase();
      if (!tail.includes(']') && EMOTES.some((e) => e.startsWith(tail))) keep = this.pending.slice(open);
    }
    this.visible(this.pending.slice(0, this.pending.length - keep.length));
    this.pending = keep;
  }

  visible(text) {
    if (!text) return;
    this.turn += text;
    if (this.prev && !this.started) {
      // Still matching what was said last turn: hold it until it diverges.
      if (this.prev.startsWith(norm(this.turn))) { this.held += text; return; }
      text = this.held + text;
      this.held = '';
    }
    this.release(text);
  }

  release(text) {
    if (!this.started) {
      text = text.replace(/^\s+/, '');
      if (!text) return;
      if (this.shown) text = ' ' + text;
      this.started = true;
    }
    this.shown = true;
    this.emit({ type: 'text', text });
  }

  /** Call at each turn boundary (after the model's message and its tool results). */
  endTurn() {
    this.flushPending();
    // Whatever is still held only repeated the previous turn, so it is dropped.
    this.held = '';
    if (norm(this.turn)) this.prev = norm(this.turn);
    this.turn = '';
    this.started = false;
  }

  finish() { this.flushPending(); this.held = ''; }

  flushPending() {
    if (this.pending) { const p = this.pending; this.pending = ''; this.visible(p); }
  }
}
