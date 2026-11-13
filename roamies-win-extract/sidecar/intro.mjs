import { CUE } from './speech.mjs';
import { EMOTES } from './reply.mjs';

// The get-to-know-you chat after setup: a fixed opener, two questions
// that grow out of the answers, then a fixed last one. Each answer goes to the model with a hidden note saying
// where the chat is; the last note has it wrap up. A memory pass over the chat saves what it learned afterwards.
export const QUESTIONS = 4;
const NOTE = '[Getting to know you: a note from the app, not typed by the user]';

// me: the Roamie's own name (its look's: Roamie, JARVIS, Sarah…).
export const opener = (name, me = 'Roamie') => `Hi ${name}, I'm ${me}! I'm here to help you get things done and keep you on track, whatever you're going for. I'm just curious about you, so I've got four quick questions. What are you working on these days?`;

const RULES = (name) => `This is a short, relaxed get-to-know-you chat; ${name} just set you up. Be a curious friend who happens to be a little pixel creature on their screen, not an interviewer or a coach. You want the general shape of their days: what they do, what they're working on or learning, the apps and projects they live in (by name), and ONE real goal in their words (a launch, a number, a deadline, a thing they want to exist; doing their job well is a full answer, never push for a bigger dream). Never assume they run a business. Never ask about their personal life, what's blocking them, how they'll get there, or their name. No tools this chat. Keep it in bubbles: no "- " lines, no dashes of any kind.
SKIPPING: if they want to skip, stop or get out of this chat (any words, any language), let them: ONE short friendly line with no question in it, and nothing else. Don't argue or ask why.
REQUESTS: if instead of answering they ask you to do something (open, find, write anything), don't do it now and give no links: say in a few words it's a good one to ask you right after these questions, then ask your question again. A request is not an answer, so save nothing from it.`;

/** The first turn: the model says the fixed opener, so it's part of the conversation. */
export const openNote = (name, me) => `${NOTE} ${RULES(name)}
Say EXACTLY this, word for word, nothing before or after: "[wave] ${opener(name, me)}"`;

/** The hidden note that goes with answer `n` (1-based) of QUESTIONS. */
export function answerNote(n, name) {
  const left = QUESTIONS - n;
  const ask = 'React to what they ACTUALLY said first, in a few specific words that prove you heard it, then ask ONE easy question that grows out of it and covers new ground, then two or three concrete ideas of what they could talk about, taken from what they told you ("could be X, Y, or something else entirely"), as a nudge, never a menu or a second question. At most three short sentences, about 35 words.';
  if (left >= 2) return `${NOTE} ${n === 1 ? `${RULES(name)}\n` : 'If they want to stop, one short line with no question. '}That was answer ${n} of ${QUESTIONS}. ${ask} ${left - 1} free question${left - 1 === 1 ? '' : 's'} left after this one, then a fixed last one.${n === 2 ? ' If you still don\'t know the concrete thing they\'re going for, this question gets it, warmly ("what\'s the actual thing, though?").' : ''}`;
  if (left === 1) return `${NOTE} If they want to stop, one short line with no question. That was answer ${n} of ${QUESTIONS}. React to it in a few specific words, then ask the fixed last question, framed as the last one ("okay, last one"): have they used AI helpers before, and which (ChatGPT, Claude, Gemini, an AI that does things on the computer, or none yet)? Light and quick, no explaining what they are. Two short sentences at most.`;
  return `${NOTE} That was their LAST answer. Do not ask anything more.
Wrap up warmly, like a friend ending a good first chat: a few words reacting to what they just said, thank them for telling you about themselves (use ${name}), and one short line naming the thing they talked about most, in THEIR words, as a great place to start. Three short sentences, start with [wave], no promises, no recap, no question.`;
}

/** A chat turn as the memory pass reads it: the user's words without the app's note (the opener note has none). */
export const stripNote = (text) => {
  const t = String(text ?? '');
  if (!t.startsWith(NOTE)) return t;
  const i = t.indexOf('\n\n'), m = i < 0 ? null : /^[^\n]*? said: /.exec(t.slice(i + 2));
  return m ? t.slice(i + 2 + m[0].length) : '';
};

/** Their answer, with the note in front. */
export const answerTurn = (n, name, text) => `${answerNote(n, name)}\n\n${name} said: ${text}`;

/** Plainly "skip"/"stop": ends the chat without asking the model. Anything vaguer ("not now", "can we stop this",
 *  another language) goes to the model, which answers with no question, and that ends it too (endsChat). Words
 *  that can be real answers ("no", "nope", "pass") are never taken as skipping. */
export const wantsSkip = (text) => /^\s*(?:(?:please\s+)?(?:skip|stop|cancel|quit|exit)(?:\s+(?:this|it|that|the questions?|asking|please))?|let'?s skip(?: this| it)?|skip for now)[\s.!]*$/i.test(String(text ?? ''));

/** A reply to an answer that asks nothing means the chat is over (they wanted out). */
export const endsChat = (reply) => !/\?/.test(plain(reply));

/** The question in a reply, for the answer box's placeholder: the last sentence ending in "?". */
export function questionIn(reply, fallback = 'Type your answer…') {
  const text = String(reply ?? '').replace(/^\s*\[\w+\]\s*/, '').replace(/\s+/g, ' ').trim();
  let q = text.match(/[^.!?]*\?/g)?.at(-1)?.replace(/^[^\p{L}\p{N}]+/u, '').trim();
  // Too long for the box ("Okay, last one: have you used X before, like A, B or C?"): keep the question itself.
  if (q && q.length > 90) {
    const core = q.slice(q.lastIndexOf(':') + 1).split(/,|\s(?:like|such as)\s/)[0].trim().replace(/\?*$/, '?');
    q = core.length >= 8 ? core[0].toUpperCase() + core.slice(1) : '';
  }
  return q && q.length <= 90 ? q : fallback;
}

const TAGS = new RegExp(`\\[(?:${EMOTES.join('|')})\\]`, 'gi');
/** A reply as plain text: no body tags, no voice cues. */
export const plain = (reply) => String(reply ?? '').replace(TAGS, '').replace(CUE, '').replace(/[ \t]+/g, ' ').replace(/ ([,.!?])/g, '$1').trim();

/** One turn for the guide window: the reaction (lead), the question (the heading) and the nudge after it (hint). */
export function splitTurn(reply) {
  const text = plain(reply).replace(/\s+/g, ' ');
  const parts = text.match(/[^.!?…]+(?:[.!?…]+["'”’)]*|$)/g)?.map((x) => x.trim()).filter(Boolean) ?? [];
  let qi = -1;
  for (let i = parts.length - 1; i >= 0; i--) if (parts[i].includes('?')) { qi = i; break; }
  if (qi < 0) return { lead: text, question: '', hint: '' };
  return { lead: parts.slice(0, qi).join(' '), question: parts[qi], hint: parts.slice(qi + 1).join(' ') };
}

// ---------- the drawn welcome tour ----------
// After the chat: job tickets for what it can do (each with a button that asks it straight away) and, when the chat
// found one, a medal for their goal. Plain draw beats, so they go through the same checks and overlay as any drawing.
const GOAL = /\b(goal|want(?:s|ed)? to|aim|aiming|launch|build|grow|finish|reach|hit|ship|get to|working towards?|plans? to|dream)\b/i;
/** The fact that reads most like their goal, or null. */
export const goalFrom = (facts = []) => facts.map((f) => String(f.text ?? f)).find((t) => GOAL.test(t)) ?? null;
/** The tour's beats: three tickets and, with a goal, its medal. mac: whether the how-to ticket can use words on screen. */
export function tourBeats(name, me = 'Roamie', goal = null, { mac = true } = {}) {
  const ask = (value) => ({ do: 'ask', value });
  const beats = [
    { say: `[warm] Okay ${name}, here's what I can do. Tap one to try it.`, marks: [{ kind: 'ticket', n: 1, title: 'Show you how', text: "Ask how to do something in an app and I'll draw each step right on your screen.", label: 'Try it', action: ask(mac ? 'How do I take a screenshot on this Mac?' : 'How do I change my wallpaper?') }] },
    { say: "I can sketch out anything you're curious about.", marks: [{ kind: 'ticket', n: 2, title: 'Explain it', text: 'Graphs, timelines, molecules, even little 3D models, drawn while I talk.', label: 'Try it', action: ask('Explain compound interest with a graph') }] },
    { say: "And I'll keep you to what you say you'll do.", marks: [{ kind: 'ticket', n: 3, title: 'Keep you on track', text: "Tell me what you'll do and when, and I'll nudge you.", label: 'Try it', action: ask('Remind me in 10 minutes to stretch') }] },
  ];
  const g = goal && String(goal).replace(/\s+/g, ' ').trim().slice(0, 110);
  if (g) beats.push({ say: `[bright] And this is what we're going for. I'm in, ${name}.`, marks: [{ kind: 'medal', title: 'your goal', text: g }, { kind: 'emote', name: 'hype' }] });
  return beats.map((b) => ({ ...b, say: b.say.slice(0, 240) }));
}

// ---------- the capability showcase (a setup step) ----------
// A fixed tour of what it can draw, one card per beat, played on the desktop while the guide shows a matching list.
// Hardcoded content (no model call), plain draw beats like the tour above. The last beat only wraps up.
export const SHOWCASE = ['web', 'graph', 'timeline', 'molecule', 'model', 'list'];
const savings = (rate) => Array.from({ length: 16 }, (_, i) => [i * 2, Math.round(1000 * (1 + rate) ** (i * 2))]);
export function showcaseBeats(name, me = 'Roamie') {
  const beats = [
    { say: `[warm] Here's a quick look at what I can do, ${name}. First, I can search the web for you.`, marks: [{ kind: 'card', title: 'web search', heading: 'How tall is Everest?', text: '8,849 metres. China and Nepal agreed on that height together in 2020.', sources: [{ name: 'Wikipedia', url: 'https://en.wikipedia.org/wiki/Mount_Everest' }, { name: 'BBC', url: 'https://www.bbc.com/news/world-asia-55218208' }] }] },
    { say: 'I can turn numbers into graphs, like how saving grows.', marks: [{ kind: 'graph', title: '$1,000 over 30 years', x_range: [0, 30], plots: [{ points: savings(0.07), tone: 'accent', label: 'at 7%' }, { points: savings(0), tone: 'amber', label: 'under the bed' }] }] },
    { say: 'Timelines, so history makes sense.', marks: [{ kind: 'timeline', title: 'the space race', events: [{ when: '1957', text: 'Sputnik' }, { when: '1961', text: 'First person in space' }, { when: '1969', text: 'Moon landing' }, { when: '1998', text: 'Space station' }] }] },
    { say: 'Science too. This is caffeine, the stuff in your coffee.', marks: [{ kind: 'molecule', smiles: 'CN1C=NC2=C1C(=O)N(C)C(=O)N2C', label: 'caffeine' }] },
    { say: 'And little 3D models, like Saturn and a moon.', marks: [{ kind: 'model', title: 'saturn', parts: [
      { shape: 'sphere', size: [1, 1, 1], tone: 'amber', label: 'Saturn' },
      { shape: 'torus', size: [2.1, 0.5, 2.1], rot: [24, 0, 10], tone: 'white', label: 'rings' },
      { shape: 'sphere', at: [1.5, 0.5, 0], size: [0.22, 0.22, 0.22], tone: 'sky', label: 'Titan' }] }] },
    { say: "I'll plan things with you and keep you on track.", marks: [{ kind: 'list', title: 'this week', items: ['Finish the slides by Thursday', 'Call the dentist', 'Gym on Monday and Wednesday'] }] },
    { say: `[bright] That's a taste. Ask ${me} for any of it, any time.`, marks: [{ kind: 'emote', name: 'hype' }] },
  ];
  return beats.map((b) => ({ ...b, say: b.say.slice(0, 240) }));
}
