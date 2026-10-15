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
  webSearchOrbStyle?: ThinkingOrbStyle;
  activeIntent?: string | null;
  activePrompt?: string | null;
}

export const AMIGO_ORB_PRESETS: {
  id: ThinkingOrbStyle;
  name: string;
  orbState: OrbState;
  desc: string;
}[] = [
  { id: "orbits", name: "Cosmic Orbits", orbState: "working", desc: "3D planetary and atomic multi-axis particle orbits" },
  { id: "rubik", name: "Puzzle Matrix", orbState: "solving", desc: "3D segmented rotating geometric bands" },
  { id: "morph", name: "Morphing Core", orbState: "shaping", desc: "3D fluid topological shape-shifting geometry" },
  { id: "ring", name: "Harmonic Rings", orbState: "breathing", desc: "3D quantum concentric breathing rings" },
  { id: "web", name: "Neural Web", orbState: "connecting", desc: "3D synaptic constellation neural network" },
  { id: "globe", name: "Radar Globe", orbState: "searching", desc: "3D scanning coordinate sphere with radar sweep" },
];

export const WEB_SEARCH_ORB_PRESETS: {
  id: ThinkingOrbStyle;
  name: string;
  orbState: OrbState;
  desc: string;
}[] = [
  { id: "globe", name: "Radar Globe", orbState: "searching", desc: "3D scanning coordinate sphere with radar sweep" },
  { id: "web", name: "Neural Web", orbState: "connecting", desc: "3D synaptic constellation with packet routing" },
  { id: "orbits", name: "Satellite Orbits", orbState: "working", desc: "3D planetary orbital tracking bands" },
  { id: "rubik", name: "Index Matrix", orbState: "solving", desc: "3D segmented rotating search index bands" },
];

export const THINKING_ORB_PRESETS = AMIGO_ORB_PRESETS;

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
  listening: "listening",
  processing: "working",
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

    // 1.5x DPR provides crisp retina rendering while cutting pixel fill rate by ~44% vs 2.0x
    const dpr = Math.min(1.5, window.devicePixelRatio || 1);
    canvas.width = ORB_RENDER_SIZE * dpr;
    canvas.height = ORB_RENDER_SIZE * dpr;
    const context = canvas.getContext("2d");
    if (!context) return;

    runningRef.current = true;
    lastTimeRef.current = performance.now();

    // Cache preset objects to eliminate thousands of GC object allocations inside the 60/120fps loop
    let lastCachedOrbState: OrbState | null = null;
    let cachedBaseSpeed = 1.0;
    let cachedScaledOpts: any = null;
    let cachedFrameRenderer: any = null;

    let lastCachedColor: string | null = null;
    let cachedTint: any = null;

    const updatePresetCache = (st: OrbState) => {
      if (lastCachedOrbState === st && cachedScaledOpts && cachedFrameRenderer) {
        return;
      }
      lastCachedOrbState = st;
      const { mode, speed: baseSpeed, opts } = resolvePreset(st, 64);
      cachedBaseSpeed = baseSpeed;
      cachedScaledOpts = scaleCounts(scaleRadii(opts, 1.1), 1.05);
      cachedFrameRenderer = MODE_FRAMES[mode];
    };

    const updateTintCache = (c: string) => {
      if (lastCachedColor === c && cachedTint) {
        return;
      }
      lastCachedColor = c;
      cachedTint = parseOrbTint(c);
    };

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
      updatePresetCache(currentOrbState);
      updateTintCache(colorRef.current);

      const motionMultiplier = prefersReducedMotion ? 0.2 : 1.0;
      const effSpeed = (stateRef.current === "listening" ? 1.62 : cachedBaseSpeed) * motionMultiplier;
      simTimeRef.current += dt * effSpeed * speedRef.current;

      // Direct zero-overhead hardware-accelerated core breathing
      if (coreRef.current) {
        const coreScale = 1.0 + smoothAudio * 0.06;
        coreRef.current.style.transform = `scale(${coreScale.toFixed(3)}) translateZ(0)`;
      }

      // Render frame without wiping canvas.width
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, ORB_RENDER_SIZE, ORB_RENDER_SIZE);

      if (cachedFrameRenderer && cachedScaledOpts) {
        const frame = cachedFrameRenderer(ORB_RENDER_SIZE, simTimeRef.current, cachedScaledOpts);
        paintFrame(context, frame, isDarkRef.current, cachedTint);
      }

      animationFrameRef.current = requestAnimationFrame(loop);
    };

    loopRef.current = loop;

    // Draw initial frame immediately so canvas is never blank if paused at mount
    updatePresetCache(orbStateRef.current);
    updateTintCache(colorRef.current);
    if (cachedFrameRenderer && cachedScaledOpts) {
      const initialFrame = cachedFrameRenderer(ORB_RENDER_SIZE, 0, cachedScaledOpts);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      paintFrame(context, initialFrame, isDarkRef.current, cachedTint);
    }

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
      className="amigo-high-resolution-orb pointer-events-none"
      style={{ willChange: "transform", transform: "translateZ(0)" }}
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
  thinkingOrbStyle = "orbits",
  webSearchOrbStyle = "globe",
  activeIntent = null,
  activePrompt = null,
}) => {
  // Detect if the active action is a Web Search
  const isWebSearch =
    activeIntent === "web_search" ||
    activeIntent === "search_web" ||
    (Boolean(activePrompt) &&
      /\b(?:search\s+(?:the\s+)?web|search\s+google|google|look\s+up\s+online|who\s+is\s+the\s+prime\s+minister|who\s+is\s+the\s+president|latest\s+news)\b/i.test(
        activePrompt || ""
      ));

  let orbState: OrbState;
  if (state === "listening") {
    orbState = "listening";
  } else if (isWebSearch && (state === "processing" || state === "working")) {
    // Distinct Web Search Orb: Radar Globe scanning or user webSearchOrbStyle
    orbState = STYLE_TO_ORB_STATE[webSearchOrbStyle] || "searching";
  } else if (state === "processing") {
    // Amigo Assistant Thinking Orb: Cosmic Orbits or user thinkingOrbStyle
    orbState = STYLE_TO_ORB_STATE[thinkingOrbStyle] || "working";
  } else {
    orbState = ORB_STATES[state] || "weaving";
  }

  // Web search gets distinctive electric cyan/blue radar tint
  let orbColor = isDark ? (ORB_COLORS_DARK[state] || "#38bdf8") : (ORB_COLORS_LIGHT[state] || "#0284c7");
  if (isWebSearch && (state === "processing" || state === "working")) {
    orbColor = isDark ? "#00f0ff" : "#0284c7";
  }

  const speed = isWebSearch && (state === "processing" || state === "working")
    ? 1.4
    : state === "processing" || state === "working"
    ? 1.2
    : 0.95;

  const coreRef = useRef<HTMLDivElement>(null);

  const ariaLabel =
    isWebSearch && (state === "processing" || state === "working")
      ? "Amigo is searching the web"
      : ORB_LABELS[state] || ORB_LABELS.idle;

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
          speed={speed}
          paused={isPaused}
          isDark={isDark}
          ariaLabel={ariaLabel}
          coreRef={coreRef}
        />
      </div>
    </div>
  );
});
