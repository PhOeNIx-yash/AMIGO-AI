import React, { useEffect, useRef } from "react";
import {
  MODE_FRAMES,
  paintFrame,
  resolvePreset,
  makeProj,
  radiusScale,
  finalizeFrame,
  Dot,
  OrbFrame,
} from "thinking-orbs/engine";
import { fibDir, scaleCounts, scaleRadii, OrbState } from "thinking-orbs";
import { AssistantState, ColorTheme, VisualizerMode } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { audioBus } from "../utils/audioBus";

interface CanvasVisualizerProps {
  mode?: VisualizerMode;
  state: AssistantState;
  colorTheme: ColorTheme;
  isDark: boolean;
  compact?: boolean;
  isPaused?: boolean;
}

// 3D Spherical & harmonic state mapping:
// idle: 'weaving' (3D braided strands + 150 ghost sphere particles)
// listening: custom harmonic voice wave sphere with real-time audio reactivity
// processing: 'solving' (3D rotating puzzle bands)
// working: 'working' (3D particle orbits around sphere)
// completed: 'composing' (3D flowing harmonic ribbon)
const ORB_STATES: Record<AssistantState, OrbState> = {
  idle: "weaving",
  listening: "listening",
  processing: "solving",
  action_card: "shaping",
  contact_picker: "connecting",
  working: "working",
  completed: "composing",
};

const ORB_LABELS: Record<AssistantState, string> = {
  idle: "Amigo is ready",
  listening: "Amigo is listening",
  processing: "Amigo is thinking",
  action_card: "Amigo is preparing choices",
  contact_picker: "Amigo is connecting details",
  working: "Amigo is working",
  completed: "Amigo completed the task",
};

const ORB_RENDER_SIZE = 256;

function parseOrbTint(color: string) {
  const match = color.trim().match(/^#([\da-f]{3}|[\da-f]{6})$/i);
  if (!match) return undefined;
  const hex = match[1].length === 3
    ? match[1].split("").map((character) => character + character).join("")
    : match[1];
  return {
    r: parseInt(hex.slice(0, 2), 16),
    g: parseInt(hex.slice(2, 4), 16),
    b: parseInt(hex.slice(4, 6), 16),
  };
}

/**
 * 3D Harmonic Voice Listening Frame Renderer
 * Designed for rock-solid 60 FPS, silky-smooth voice amplitude reactivity,
 * zero z-fighting/flicker, and seamless continuity with the idle Fibonacci particle sphere.
 */
function frameListening(
  size: number,
  t: number,
  audioLevel: number,
  rBase: number = 1.15,
  rDepth: number = 1.65
): OrbFrame {
  const cx = size / 2;
  const cy = size / 2;
  const R = (size / 2) * 0.76;
  const camTilt = 0.32;
  const pt = makeProj(t * 0.35, camTilt, cx, cy, 1);
  const rs = radiusScale(size, 0.6);
  const dots: Dot[] = [];

  // 1. 3D Fibonacci Ghost Hull: 84 sparkling particles forming the sphere's translucent body
  const ghostN = 84;
  const audioPulse = 1 + audioLevel * 0.10;
  for (let i = 0; i < ghostN; i++) {
    const d = fibDir(i, ghostN);
    const rG = R * audioPulse * (1 + 0.025 * Math.sin(t * 1.5 + i * 0.7));
    const [px, py, z] = pt(d[0] * rG, d[1] * rG, d[2] * rG);
    const depth = (z / R + 1) / 2;
    dots.push({
      x: px,
      y: py,
      z,
      r: (0.75 + 0.35 * audioLevel) * rs,
      white: 0.76 + 0.18 * audioLevel,
      a: 0.15 + 0.32 * depth + 0.12 * audioLevel,
    });
  }

  // 2. Harmonic Fluid Voice Strands: 3 continuous flowing wave ribbons wrapped around the sphere
  const strands = 3;
  const strandPoints = 42;
  const waveAmp = 0.04 + audioLevel * 0.16;
  for (let s = 0; s < strands; s++) {
    const phase = (s / strands) * Math.PI * 2;
    for (let i = 0; i < strandPoints; i++) {
      const u = (i / strandPoints) * 2 - 1; // -1 to 1 latitude
      const lat = u * (Math.PI * 0.44);
      const cosLat = Math.cos(lat);
      const sinLat = Math.sin(lat);
      const lon = u * Math.PI * 3.0 + t * 0.5 + phase;

      // Smooth organic harmonic wave equation
      const wave = 1 + waveAmp * Math.sin(u * 4.2 - t * 2.2 + phase * 2);
      const rr = R * wave * audioPulse;
      const x = cosLat * Math.cos(lon) * rr;
      const y = sinLat * rr;
      const z0 = cosLat * Math.sin(lon) * rr;

      const [px, py, z] = pt(x, y, z0);
      const depth = (z / R + 1) / 2;
      const endFade = 1 - Math.abs(u) * 0.45;
      dots.push({
        x: px,
        y: py,
        z,
        r: ((rBase + rDepth * depth) * (1 + 0.3 * audioLevel)) * rs,
        white: Math.max(0, 0.58 - 0.48 * depth + 0.28 * audioLevel),
        a: endFade * (0.42 + 0.58 * depth),
      });
    }
  }

  return finalizeFrame(dots, [], 0.3);
}

interface HighResolutionOrbProps {
  state: AssistantState;
  orbState: OrbState;
  color: string;
  isDark: boolean;
  speed: number;
  paused: boolean;
  ariaLabel: string;
  haloRef: React.RefObject<HTMLDivElement | null>;
  coreRef: React.RefObject<HTMLDivElement | null>;
}

const HighResolutionOrb: React.FC<HighResolutionOrbProps> = ({
  state,
  orbState,
  color,
  isDark,
  speed,
  paused,
  ariaLabel,
  haloRef,
  coreRef,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const audioTargetRef = useRef(0);
  const audioSmoothRef = useRef(0);
  const simTimeRef = useRef(0);
  const lastTimeRef = useRef(0);

  // Subscribe to live audio levels with smooth decay
  useEffect(() => {
    return audioBus.subscribe((level) => {
      audioTargetRef.current = Math.min(Math.max(level, 0), 1);
    });
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = ORB_RENDER_SIZE * dpr;
    canvas.height = ORB_RENDER_SIZE * dpr;
    const context = canvas.getContext("2d");
    if (!context) return;

    const { mode, speed: baseSpeed, opts } = resolvePreset(orbState, 64);
    // Moderate, high-performance scaling to maintain rock-solid 60fps with rich 3D density
    const scaledOpts = scaleCounts(scaleRadii(opts, 1.15), 1.25);
    const frameRenderer = MODE_FRAMES[mode];
    const tint = parseOrbTint(color);
    let animationFrame = 0;
    let running = true;

    lastTimeRef.current = performance.now();

    const loop = (now: number) => {
      if (!running) return;

      // Delta time in seconds, capped at 40ms to prevent jumps on tab resume
      const dt = Math.min(Math.max((now - lastTimeRef.current) / 1000, 0), 0.04);
      lastTimeRef.current = now;

      // Asymmetric DSP envelope follower: fast attack (24ms), graceful natural decay (60ms)
      const targetAudio = state === "listening" ? audioTargetRef.current : 0;
      const attack = 0.24;
      const decay = 0.06;
      const k = targetAudio > audioSmoothRef.current ? attack : decay;
      audioSmoothRef.current += (targetAudio - audioSmoothRef.current) * k;
      const smoothAudio = audioSmoothRef.current;

      // STABLE CLOCK: Uniform time progression ensures silky-smooth 60fps with ZERO stutter or tearing
      // Base speed is steady and calm across all states (normalized listening speed)
      const effSpeed = state === "listening" ? 1.62 : baseSpeed;
      simTimeRef.current += dt * effSpeed * speed;

      // Direct zero-overhead hardware-accelerated halo & core breathing (no React state updates)
      if (haloRef.current) {
        const haloScale = 1.0 + smoothAudio * 0.22;
        const haloOpacity = isDark
          ? 0.30 + smoothAudio * 0.25
          : 0.22 + smoothAudio * 0.18;
        haloRef.current.style.setProperty("--amigo-orb-scale", haloScale.toFixed(3));
        haloRef.current.style.opacity = haloOpacity.toFixed(3);
      }
      if (coreRef.current) {
        const coreScale = 1.0 + smoothAudio * 0.06;
        coreRef.current.style.setProperty("--amigo-orb-core-scale", coreScale.toFixed(3));
      }

      // Render frame
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, ORB_RENDER_SIZE, ORB_RENDER_SIZE);

      const frame = state === "listening"
        ? frameListening(ORB_RENDER_SIZE, simTimeRef.current, smoothAudio)
        : frameRenderer(ORB_RENDER_SIZE, simTimeRef.current, scaledOpts);

      paintFrame(context, frame, isDark, tint);

      if (!paused) {
        animationFrame = requestAnimationFrame(loop);
      }
    };

    if (!paused) {
      animationFrame = requestAnimationFrame(loop);
    }

    return () => {
      running = false;
      cancelAnimationFrame(animationFrame);
    };
  }, [color, coreRef, haloRef, isDark, orbState, paused, speed, state]);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={ariaLabel}
      className="amigo-high-resolution-orb"
      width={ORB_RENDER_SIZE}
      height={ORB_RENDER_SIZE}
    />
  );
};

export const CanvasVisualizer: React.FC<CanvasVisualizerProps> = React.memo(({
  state,
  colorTheme,
  isDark,
  compact = false,
  isPaused = false,
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const orbState = ORB_STATES[state] || "weaving";
  const orbColor = isDark ? theme.accent : theme.primary;
  const haloRef = useRef<HTMLDivElement>(null);
  const coreRef = useRef<HTMLDivElement>(null);

  return (
    <div
      className={`amigo-orb-stage absolute inset-0 z-0 pointer-events-none overflow-hidden transition-all duration-500 ${
        compact ? "compact opacity-85" : "opacity-100"
      }`}
      aria-hidden="true"
    >
      <div ref={haloRef} className="amigo-orb-halo" style={{ background: theme.glow }} />
      <div ref={coreRef} className="amigo-orb-core">
        <HighResolutionOrb
          state={state}
          orbState={orbState}
          color={orbColor}
          speed={state === "processing" || state === "working" ? 1.25 : 0.95}
          paused={isPaused}
          isDark={isDark}
          ariaLabel={ORB_LABELS[state] || ORB_LABELS.idle}
          haloRef={haloRef}
          coreRef={coreRef}
        />
      </div>
    </div>
  );
});
