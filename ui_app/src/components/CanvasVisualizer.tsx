import React, { useEffect, useRef } from "react";
import {
  MODE_FRAMES,
  paintFrame,
  resolvePreset,
} from "thinking-orbs/engine";
import { scaleCounts, scaleRadii, OrbState } from "thinking-orbs";
import { AssistantState, ColorTheme, VisualizerMode, ThinkingOrbStyle } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { audioBus } from "../utils/audioBus";

export interface CanvasVisualizerProps {
  mode?: VisualizerMode;
  state: AssistantState;
  colorTheme: ColorTheme;
  isDark: boolean;
  compact?: boolean;
  isPaused?: boolean;
  thinkingOrbStyle?: ThinkingOrbStyle;
}

export const THINKING_ORB_PRESETS: {
  id: ThinkingOrbStyle;
  name: string;
  orbState: OrbState;
  desc: string;
}[] = [
  { id: "globe", name: "Radar Globe", orbState: "searching", desc: "3D scanning coordinate sphere with radar sweep" },
  { id: "orbits", name: "Cosmic Orbits", orbState: "working", desc: "3D planetary and atomic multi-axis particle orbits" },
  { id: "web", name: "Neural Web", orbState: "connecting", desc: "3D synaptic constellation neural network" },
  { id: "morph", name: "Morphing Core", orbState: "shaping", desc: "3D fluid topological shape-shifting geometry" },
  { id: "ring", name: "Harmonic Rings", orbState: "breathing", desc: "3D quantum concentric breathing rings" },
  { id: "rubik", name: "Puzzle Matrix", orbState: "solving", desc: "3D segmented rotating geometric bands" },
];

export const STYLE_TO_ORB_STATE: Record<ThinkingOrbStyle, OrbState> = {
  globe: "searching",
  orbits: "working",
  web: "connecting",
  morph: "shaping",
  ring: "breathing",
  rubik: "solving",
};

// 3D Spherical state mapping
const ORB_STATES: Record<AssistantState, OrbState> = {
  idle: "weaving",
  listening: "weaving",
  processing: "searching",
  action_card: "shaping",
  contact_picker: "connecting",
  working: "working",
  completed: "composing",
  generated_content: "composing",
};

const ORB_LABELS: Record<AssistantState, string> = {
  idle: "Amigo is ready",
  listening: "Amigo is listening",
  processing: "Amigo is thinking",
  action_card: "Amigo is preparing choices",
  contact_picker: "Amigo is connecting details",
  working: "Amigo is working",
  completed: "Amigo completed the task",
  generated_content: "Amigo generated content for review",
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

const ORB_COLORS_DARK: Record<AssistantState, string> = {
  listening: "#00f0ff",
  processing: "#60a5fa",
  working: "#818cf8",
  completed: "#2dd4bf",
  generated_content: "#2dd4bf",
  action_card: "#38bdf8",
  contact_picker: "#38bdf8",
  idle: "#38bdf8",
};

const ORB_COLORS_LIGHT: Record<AssistantState, string> = {
  listening: "#0891b2",
  processing: "#2563eb",
  working: "#4338ca",
  completed: "#0d9488",
  generated_content: "#0d9488",
  action_card: "#0284c7",
  contact_picker: "#0284c7",
  idle: "#0284c7",
};



interface HighResolutionOrbProps {
  state: AssistantState;
  orbState: OrbState;
  color: string;
  isDark: boolean;
  speed: number;
  paused: boolean;
  ariaLabel: string;
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
  coreRef,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const audioTargetRef = useRef(0);
  const audioSmoothRef = useRef(0);
  const simTimeRef = useRef(0);
  const lastTimeRef = useRef(0);

  // Dynamic references allow continuous rendering loop without tearing down canvas context on prop changes
  const stateRef = useRef(state);
  stateRef.current = state;
  const orbStateRef = useRef(orbState);
  orbStateRef.current = orbState;
  const colorRef = useRef(color);
  colorRef.current = color;
  const isDarkRef = useRef(isDark);
  isDarkRef.current = isDark;
  const speedRef = useRef(speed);
  speedRef.current = speed;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  // Reduced motion preference handling
  const prefersReducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Subscribe to live audio levels with smooth decay
  useEffect(() => {
    return audioBus.subscribe((level) => {
      audioTargetRef.current = Math.min(Math.max(level, 0), 1);
    });
  }, []);

  const loopRef = useRef<((now: number) => void) | null>(null);
  const isLoopActiveRef = useRef(false);
  const runningRef = useRef(true);
  const animationFrameRef = useRef(0);

  useEffect(() => {
    pausedRef.current = paused;
    if (!paused && canvasRef.current && runningRef.current && !isLoopActiveRef.current && loopRef.current) {
      lastTimeRef.current = performance.now();
      isLoopActiveRef.current = true;
      animationFrameRef.current = requestAnimationFrame(loopRef.current);
    }
  }, [paused]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = ORB_RENDER_SIZE * dpr;
    canvas.height = ORB_RENDER_SIZE * dpr;
    const context = canvas.getContext("2d");
    if (!context) return;

    runningRef.current = true;
    lastTimeRef.current = performance.now();

    const loop = (now: number) => {
      if (!runningRef.current) {
        isLoopActiveRef.current = false;
        return;
      }

      if (pausedRef.current) {
        // Suspend loop when paused to completely stop CPU/GPU load
        isLoopActiveRef.current = false;
        return;
      }

      isLoopActiveRef.current = true;

      // Delta time in seconds, capped at 40ms to prevent jumps on tab resume
      const dt = Math.min(Math.max((now - lastTimeRef.current) / 1000, 0), 0.04);
      lastTimeRef.current = now;

      // Physical DSP envelope follower in seconds (consistent across 60Hz, 120Hz, 144Hz displays)
      const targetAudio = stateRef.current === "listening" ? audioTargetRef.current : 0;
      const timeConst = targetAudio > audioSmoothRef.current ? 0.024 : 0.060; // 24ms attack, 60ms decay
      const alpha = 1 - Math.exp(-dt / timeConst);
      audioSmoothRef.current += (targetAudio - audioSmoothRef.current) * alpha;
      const smoothAudio = audioSmoothRef.current;

      const currentOrbState = orbStateRef.current;
      const { mode, speed: baseSpeed, opts } = resolvePreset(currentOrbState, 64);
      const scaledOpts = scaleCounts(scaleRadii(opts, 1.15), 1.25);
      const frameRenderer = MODE_FRAMES[mode];
      const tint = parseOrbTint(colorRef.current);
      const motionMultiplier = prefersReducedMotion ? 0.2 : 1.0;

      const effSpeed = (stateRef.current === "listening" ? 1.62 : baseSpeed) * motionMultiplier;
      simTimeRef.current += dt * effSpeed * speedRef.current;

      // Direct zero-overhead hardware-accelerated core breathing
      if (coreRef.current) {
        const coreScale = 1.0 + smoothAudio * 0.06;
        coreRef.current.style.transform = `scale(${coreScale.toFixed(3)}) translateZ(0)`;
      }

      // Render frame without wiping canvas.width
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, ORB_RENDER_SIZE, ORB_RENDER_SIZE);

      const frame = frameRenderer(ORB_RENDER_SIZE, simTimeRef.current, scaledOpts);

      paintFrame(context, frame, isDarkRef.current, tint);

      animationFrameRef.current = requestAnimationFrame(loop);
    };

    loopRef.current = loop;

    // Draw initial frame immediately so canvas is never blank if paused at mount
    const { mode, opts } = resolvePreset(orbStateRef.current, 64);
    const scaledOpts = scaleCounts(scaleRadii(opts, 1.15), 1.25);
    const frameRenderer = MODE_FRAMES[mode];
    const initialFrame = frameRenderer(ORB_RENDER_SIZE, 0, scaledOpts);
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    paintFrame(context, initialFrame, isDarkRef.current, parseOrbTint(colorRef.current));

    if (!pausedRef.current) {
      isLoopActiveRef.current = true;
      animationFrameRef.current = requestAnimationFrame(loop);
    }

    return () => {
      runningRef.current = false;
      isLoopActiveRef.current = false;
      cancelAnimationFrame(animationFrameRef.current);
      loopRef.current = null;
    };
  }, []);

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
  thinkingOrbStyle = "globe",
}) => {
  const baseOrbState = ORB_STATES[state] || "weaving";
  const orbState: OrbState =
    state === "processing"
      ? (STYLE_TO_ORB_STATE[thinkingOrbStyle] || "searching")
      : baseOrbState;

  const orbColor = isDark ? (ORB_COLORS_DARK[state] || "#38bdf8") : (ORB_COLORS_LIGHT[state] || "#0284c7");
  const coreRef = useRef<HTMLDivElement>(null);

  return (
    <div
      className={`amigo-orb-stage relative flex items-center justify-center z-0 pointer-events-none transition-all duration-500 ${
        compact ? "compact opacity-0 scale-75" : "opacity-100 scale-100"
      }`}
    >
      <div ref={coreRef} className="amigo-orb-core">
        <HighResolutionOrb
          state={state}
          orbState={orbState}
          color={orbColor}
          speed={state === "processing" || state === "working" ? 1.25 : 0.95}
          paused={isPaused}
          isDark={isDark}
          ariaLabel={ORB_LABELS[state] || ORB_LABELS.idle}
          coreRef={coreRef}
        />
      </div>
    </div>
  );
});
