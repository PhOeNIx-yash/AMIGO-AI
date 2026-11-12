// Tiny chiptune sound kit, synthesised live with Web Audio. Soft pulse and triangle voices through
// one quiet master and a low-pass, so nothing is shrill. Silent until the first click or key press.
(() => {
  const R = window.Roamie;
  const KEY = 'roamie-sound';
  const NOTE = { C5: 523.25, E5: 659.25, G5: 783.99, A5: 880, C6: 1046.5, E6: 1318.5, G6: 1568 };
  const GAP = { hop: 0.12, stomp: 0.2, bonk: 0.14, pip: 0.08, snore: 1, blip: 0.05, tick: 0.2, key: 0.03, scribble: 0.06, ping: 0.08 };
  let live = null, enabled = true, unlocked = false;
  try { enabled = localStorage.getItem(KEY) !== 'off'; } catch {}
  const last = {};

  const rig = (ac, dest) => {
    const lp = ac.createBiquadFilter();
    lp.type = 'lowpass'; lp.frequency.value = 4800; lp.Q.value = 0.4;
    const out = ac.createGain();
    out.gain.value = 0.22;
    out.connect(lp); lp.connect(dest);
    const pulse = (duty) => {
      const n = 32, re = new Float32Array(n + 1), im = new Float32Array(n + 1);
      for (let k = 1; k <= n; k++) re[k] = (2 * Math.sin(Math.PI * k * duty)) / (Math.PI * k);
      return ac.createPeriodicWave(re, im);
    };
    const nb = ac.createBuffer(1, ac.sampleRate * 0.5, ac.sampleRate);
    const ch = nb.getChannelData(0);
    for (let i = 0; i < ch.length; i++) ch[i] = Math.random() * 2 - 1;
    return { ac, out, p25: pulse(0.25), p12: pulse(0.125), noise: nb };
  };

  // One enveloped voice. f1 glides (or steps, for an 8-bit feel); vib adds a wobble.
  const tone = (X, t, { wave = 'triangle', f0, f1, dur, vol = 0.4, attack = 0.004, steps = 0, vib = 0, vibRate = 12 }) => {
    const { ac, out } = X;
    const o = ac.createOscillator();
    if (wave === 'p25') o.setPeriodicWave(X.p25); else if (wave === 'p12') o.setPeriodicWave(X.p12); else o.type = wave;
    o.frequency.setValueAtTime(f0, t);
    if (f1) {
      if (steps) for (let i = 1; i <= steps; i++) o.frequency.setValueAtTime(f0 + ((f1 - f0) * i) / steps, t + (dur * 0.8 * i) / steps);
      else o.frequency.exponentialRampToValueAtTime(f1, t + dur);
    }
    if (vib) {
      const l = ac.createOscillator(), lg = ac.createGain();
      l.frequency.value = vibRate; lg.gain.value = vib;
      l.connect(lg); lg.connect(o.frequency); l.start(t); l.stop(t + dur + 0.05);
    }
    const g = ac.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(vol, t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(out);
    o.start(t); o.stop(t + dur + 0.05);
  };
  const hiss = (X, t, { dur, vol = 0.3, type = 'bandpass', f0 = 1200, f1, q = 1 }) => {
    const { ac, out } = X;
    const s = ac.createBufferSource(); s.buffer = X.noise;
    const f = ac.createBiquadFilter(); f.type = type; f.Q.value = q;
    f.frequency.setValueAtTime(f0, t);
    if (f1) f.frequency.exponentialRampToValueAtTime(f1, t + dur);
    const g = ac.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(vol, t + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    s.connect(f); f.connect(g); g.connect(out);
    s.start(t); s.stop(t + dur + 0.05);
  };

  const SOUNDS = {
    boop: (X, t) => { tone(X, t, { wave: 'p25', f0: 760, f1: 1140, dur: 0.07, vol: 0.45 }); tone(X, t + 0.06, { wave: 'p25', f0: 1520, dur: 0.05, vol: 0.25 }); },
    grab: (X, t) => tone(X, t, { f0: 480, f1: 880, dur: 0.09, vol: 0.5 }),
    pip: (X, t) => tone(X, t, { f0: 1400, dur: 0.035, vol: 0.18 }),
    whoosh: (X, t) => hiss(X, t, { dur: 0.22, vol: 0.32, f0: 2400, f1: 500, q: 1.2 }),
    bonk: (X, t) => {
      tone(X, t, { f0: 260, f1: 120, dur: 0.1, vol: 0.6 });
      hiss(X, t, { dur: 0.03, vol: 0.25, type: 'lowpass', f0: 900 });
      tone(X, t + 0.12, { wave: 'sine', f0: 900, f1: 500, dur: 0.45, vol: 0.16, vib: 40, vibRate: 14 });
    },
    wave: (X, t) => { tone(X, t, { wave: 'p12', f0: NOTE.C6, dur: 0.07, vol: 0.32 }); tone(X, t + 0.085, { wave: 'p12', f0: NOTE.E6, dur: 0.11, vol: 0.32 }); },
    hype: (X, t) => {
      [NOTE.C5, NOTE.E5, NOTE.G5, NOTE.C6].forEach((f, i) => tone(X, t + i * 0.06, { wave: 'p25', f0: f, dur: 0.055, vol: 0.28 }));
      tone(X, t + 0.26, { f0: NOTE.G6, dur: 0.06, vol: 0.18 });
    },
    hop: (X, t) => tone(X, t, { f0: 330, f1: 220, dur: 0.05, vol: 0.28 }),
    remind: (X, t) => {
      for (const d of [0, 0.22]) { tone(X, t + d, { wave: 'sine', f0: NOTE.G6, dur: 0.2, vol: 0.28 }); tone(X, t + d, { f0: NOTE.G5, dur: 0.12, vol: 0.1 }); }
    },
    upset: (X, t) => { tone(X, t, { wave: 'p25', f0: 196, f1: 155, dur: 0.16, vol: 0.22 }); tone(X, t + 0.18, { wave: 'p25', f0: 165, f1: 130, dur: 0.2, vol: 0.22 }); },
    stomp: (X, t) => { tone(X, t, { f0: 110, f1: 60, dur: 0.09, vol: 0.5 }); hiss(X, t, { dur: 0.05, vol: 0.2, type: 'lowpass', f0: 300 }); },
    motivate: (X, t) => { tone(X, t, { wave: 'p25', f0: 392, f1: 1175, dur: 0.3, vol: 0.26, steps: 8 }); tone(X, t + 0.3, { f0: NOTE.G6, dur: 0.12, vol: 0.16 }); },
    yawn: (X, t) => tone(X, t, { f0: 620, f1: 310, dur: 0.55, vol: 0.18, attack: 0.08, vib: 8, vibRate: 5 }),
    snore: (X, t) => tone(X, t, { wave: 'sine', f0: 180, f1: 140, dur: 0.6, vol: 0.08, attack: 0.25 }),
    wake: (X, t) => tone(X, t, { f0: 520, f1: 980, dur: 0.1, vol: 0.24 }),
    // Talking: a tiny voiced blip per syllable, pitch set by the caller so speech has a melody.
    blip: (X, t, f = 1) => tone(X, t, { wave: 'p25', f0: 520 * f, f1: 560 * f, dur: 0.035, vol: 0.13 }),
    pop: (X, t) => { tone(X, t, { wave: 'sine', f0: 380, f1: 900, dur: 0.07, vol: 0.32 }); tone(X, t + 0.03, { f0: 1300, dur: 0.03, vol: 0.1 }); },
    poof: (X, t) => { hiss(X, t, { dur: 0.16, vol: 0.12, f0: 3200, f1: 900, q: 0.8 }); tone(X, t, { wave: 'sine', f0: 700, f1: 300, dur: 0.1, vol: 0.12 }); },
    hmm: (X, t) => { tone(X, t, { wave: 'sine', f0: 330, f1: 300, dur: 0.22, vol: 0.12, attack: 0.04, vib: 6, vibRate: 7 }); tone(X, t + 0.24, { wave: 'sine', f0: 370, f1: 340, dur: 0.24, vol: 0.12, attack: 0.04, vib: 6, vibRate: 7 }); },
    tick: (X, t) => tone(X, t, { wave: 'sine', f0: 1800, dur: 0.02, vol: 0.06 }),
    toolstart: (X, t) => { tone(X, t, { wave: 'p12', f0: 990, dur: 0.035, vol: 0.14 }); tone(X, t + 0.04, { wave: 'p12', f0: 1320, dur: 0.04, vol: 0.12 }); },
    done: (X, t) => { tone(X, t, { wave: 'p25', f0: NOTE.E6, dur: 0.06, vol: 0.18 }); tone(X, t + 0.07, { wave: 'p25', f0: NOTE.A5 * 2, dur: 0.12, vol: 0.18 }); },
    error: (X, t) => { tone(X, t, { wave: 'p25', f0: 392, dur: 0.09, vol: 0.18 }); tone(X, t + 0.1, { wave: 'p25', f0: 311, dur: 0.16, vol: 0.18 }); },
    focus: (X, t) => { for (let i = 0; i < 3; i++) tone(X, t + i * 0.09, { wave: 'sine', f0: 2200, dur: 0.02, vol: 0.08 }); },
    shutter: (X, t) => { hiss(X, t, { dur: 0.05, vol: 0.3, type: 'highpass', f0: 2500 }); tone(X, t, { f0: 1600, f1: 700, dur: 0.05, vol: 0.18 }); hiss(X, t + 0.07, { dur: 0.04, vol: 0.2, type: 'highpass', f0: 3000 }); },
    scan: (X, t) => { tone(X, t, { wave: 'sine', f0: 620, f1: 1500, dur: 0.5, vol: 0.05, attack: 0.03 }); tone(X, t + 0.02, { wave: 'p12', f0: 1240, f1: 3000, dur: 0.45, vol: 0.02, attack: 0.03 }); },
    gulp: (X, t) => tone(X, t, { wave: 'sine', f0: 300, f1: 620, dur: 0.09, vol: 0.28 }),
    page: (X, t) => hiss(X, t, { dur: 0.12, vol: 0.18, f0: 4200, f1: 1800, q: 0.7 }),
    scribble: (X, t) => hiss(X, t, { dur: 0.05, vol: 0.07, f0: 5000, q: 2 }),
    crunch: (X, t) => { for (let i = 0; i < 4; i++) hiss(X, t + i * 0.05, { dur: 0.04, vol: 0.16, type: 'lowpass', f0: 1400 - i * 200 }); },
    key: (X, t, f = 1) => { hiss(X, t, { dur: 0.015, vol: 0.12, f0: 3200 * f, q: 3 }); tone(X, t, { f0: 1700 * f, dur: 0.012, vol: 0.05 }); },
    ping: (X, t) => tone(X, t, { wave: 'sine', f0: 1760, f1: 1900, dur: 0.06, vol: 0.1 }),
    listen: (X, t) => { tone(X, t, { wave: 'sine', f0: 660, dur: 0.07, vol: 0.16 }); tone(X, t + 0.07, { wave: 'sine', f0: 990, dur: 0.1, vol: 0.16 }); },
    heard: (X, t) => { tone(X, t, { wave: 'p12', f0: 1320, dur: 0.04, vol: 0.12 }); tone(X, t + 0.05, { wave: 'p12', f0: 1760, dur: 0.06, vol: 0.12 }); },
    // Presence: three entrances and three exits, each under half a second and quiet.
    materialize: (X, t) => {
      hiss(X, t, { dur: 0.42, vol: 0.06, f0: 1800, f1: 7000, q: 1.4 });
      [1046.5, 1318.5, 1568, 2093].forEach((f, i) => tone(X, t + 0.06 + i * 0.075, { wave: 'sine', f0: f, dur: 0.09, vol: 0.09 }));
    },
    dematerialize: (X, t) => {
      [2093, 1568, 1318.5, 1046.5].forEach((f, i) => tone(X, t + i * 0.07, { wave: 'sine', f0: f, dur: 0.08, vol: 0.08 }));
      hiss(X, t + 0.05, { dur: 0.5, vol: 0.05, f0: 6000, f1: 1500, q: 1.4 });
    },
    crt_on: (X, t) => {
      hiss(X, t, { dur: 0.012, vol: 0.18, type: 'lowpass', f0: 2500 });
      tone(X, t + 0.01, { wave: 'sine', f0: 140, f1: 880, dur: 0.2, vol: 0.12 });
      tone(X, t + 0.03, { wave: 'p12', f0: 2200, dur: 0.05, vol: 0.05 });
    },
    crt_off: (X, t) => {
      tone(X, t, { wave: 'sine', f0: 900, f1: 90, dur: 0.24, vol: 0.12 });
      tone(X, t + 0.3, { wave: 'sine', f0: 1500, dur: 0.035, vol: 0.08 });
    },
    drop: (X, t) => tone(X, t, { wave: 'sine', f0: 1500, f1: 520, dur: 0.32, vol: 0.06 }),
    land: (X, t) => { tone(X, t, { f0: 210, f1: 80, dur: 0.1, vol: 0.34 }); hiss(X, t, { dur: 0.05, vol: 0.1, type: 'lowpass', f0: 700 }); },
    jump: (X, t) => tone(X, t, { f0: 240, f1: 760, dur: 0.18, vol: 0.2, vib: 25, vibRate: 22 }),
    expand: (X, t) => { [0, 1, 2].forEach((i) => tone(X, t + i * 0.035, { wave: 'p12', f0: 700 + i * 260, dur: 0.04, vol: 0.14 })); },
  };

  const start = () => { if (!live) { const ac = new AudioContext(); live = rig(ac, ac.destination); } live.ac.resume(); unlocked = true; };
  if (window.ROAMIE_AUTOSOUND) start();
  addEventListener('pointerdown', start, { capture: true });
  addEventListener('keydown', start, { capture: true });

  R.sfx = {
    names: Object.keys(SOUNDS),
    play(name, arg) {
      const fn = SOUNDS[name];
      if (!fn || !enabled || !unlocked || !live || live.ac.state !== 'running') return;
      const now = live.ac.currentTime;
      if (now - (last[name] ?? -1) < (GAP[name] ?? 0.09)) return;
      last[name] = now;
      fn(live, now + 0.005, arg);
    },
    // Offline render, used to check length and loudness of each sound.
    async render(name, secs = 1.2, arg) {
      const ac = new OfflineAudioContext(1, Math.ceil(44100 * secs), 44100);
      SOUNDS[name](rig(ac, ac.destination), 0.01, arg);
      return (await ac.startRendering()).getChannelData(0);
    },
    get enabled() { return enabled; },
    set enabled(v) { enabled = v; try { localStorage.setItem(KEY, v ? 'on' : 'off'); } catch {} },
    bindToggle(btn) {
      const sync = () => btn.setAttribute('aria-pressed', String(enabled));
      btn.addEventListener('click', () => { R.sfx.enabled = !enabled; sync(); if (enabled) R.sfx.play('pip'); });
      sync();
    },
  };
})();
