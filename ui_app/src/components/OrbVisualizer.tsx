import React, { useEffect, useRef } from "react";
import { Orb, type OrbState } from "@yogesharc/thinking-orbs";
import { AssistantState, ColorTheme, VisualizerMode, ThinkingOrbStyle } from "../types";
import { audioBus } from "../utils/audioBus";

export interface OrbVisualizerProps {
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

export type CanvasVisualizerProps = OrbVisualizerProps;

export const AMIGO_ORB_PRESETS: {
  id: ThinkingOrbStyle;
  name: string;
  orbState: OrbState;
  desc: string;
}[] = [
  { id: "orbits", name: "Reasoning Walk", orbState: "reasoning", desc: "Signature 3D reasoning thinking orb" },
  { id: "rubik", name: "Dual Constellation", orbState: "reasoning", desc: "Twin thinking walkers exploring simultaneously" },
  { id: "morph", name: "Compacting Core", orbState: "compacting", desc: "Dynamic squeeze and pack compression animation" },
  { id: "ring", name: "Gyroscopic Spin", orbState: "working", desc: "Gyroscopic 3D axis orbital tilt" },
  { id: "web", name: "Spiral Constellation", orbState: "background", desc: "Hypnotic spiral dot flow" },
  { id: "globe", name: "Surge Pulse", orbState: "retrying", desc: "Rhythmic surge and rewind wave" },
];

export const WEB_SEARCH_ORB_PRESETS: {
  id: ThinkingOrbStyle;
  name: string;
  orbState: OrbState;
  desc: string;
}[] = [
  { id: "globe", name: "Search Lens", orbState: "searching", desc: "Sweeping optical search lens scanning the sphere" },
  { id: "orbits", name: "Lighthouse Radar", orbState: "searching", desc: "Rotating 360° lighthouse radar beam" },
  { id: "web", name: "Twin Searchers", orbState: "reasoning", desc: "Dual synaptic walkers tracing queries" },
  { id: "rubik", name: "Deep Scan", orbState: "searching", desc: "Focused coordinate surface search" },
];

export const THINKING_ORB_PRESETS = AMIGO_ORB_PRESETS;

export const STYLE_TO_ORB_STATE: Record<ThinkingOrbStyle, OrbState> = {
  globe: "searching",
  orbits: "reasoning",
  web: "background",
  morph: "compacting",
  ring: "working",
  rubik: "reasoning",
};

const ORB_LABELS: Record<AssistantState, string> = {
  idle: "Amigo is waiting",
  listening: "Amigo is listening",
  processing: "Amigo is thinking",
  action_card: "Amigo is preparing choices",
  contact_picker: "Amigo is connecting details",
  working: "Amigo is working",
  completed: "Amigo is ready",
  generated_content: "Amigo generated content for review",
};

const ORB_COLORS_DARK: Record<AssistantState, string> = {
  listening: "#00ffff",
  processing: "#67e8f9",
  working: "#a5b4fc",
  completed: "#34d399",
  generated_content: "#34d399",
  action_card: "#38bdf8",
  contact_picker: "#38bdf8",
  idle: "#38bdf8",
};

const ORB_COLORS_LIGHT: Record<AssistantState, string> = {
  listening: "#06b6d4",
  processing: "#3b82f6",
  working: "#6366f1",
  completed: "#10b981",
  generated_content: "#10b981",
  action_card: "#0284c7",
  contact_picker: "#0284c7",
  idle: "#0284c7",
};

export interface OrbConfig {
  state: OrbState;
  variant?: any;
}

/**
 * Maps Amigo assistant activities to the dedicated Thinking Orbs animations:
 * - Searching -> "searching" (search lens / lighthouse)
 * - Thinking  -> "reasoning" (signature dot walk / twins)
 * - Waiting   -> "waiting"   (comet spiralling around sphere)
 * - Listening -> "using" ("record" audio pulse)
 * - Working   -> "working"   (gyroscopic 3D orbit)
 */
export function getOrbConfig(
  assistantState: AssistantState,
  isWebSearch: boolean,
  thinkingOrbStyle?: ThinkingOrbStyle,
  webSearchOrbStyle?: ThinkingOrbStyle
): OrbConfig {
  // 1. Searching (live online web search or search intent)
  if (isWebSearch) {
    if (webSearchOrbStyle === "orbits") {
      return { state: "searching", variant: "lighthouse" };
    }
    if (webSearchOrbStyle === "web") {
      return { state: "reasoning", variant: "twins" };
    }
    return { state: "searching", variant: "default" };
  }

  // 2. Thinking / Reasoning
  if (assistantState === "processing") {
    switch (thinkingOrbStyle) {
      case "rubik":
        return { state: "reasoning", variant: "twins" };
      case "morph":
        return { state: "compacting", variant: "squeeze" };
      case "ring":
        return { state: "working", variant: "gyro" };
      case "web":
        return { state: "background", variant: "spiral" };
      case "globe":
        return { state: "retrying", variant: "surge" };
      case "orbits":
      default:
        return { state: "reasoning", variant: "default" };
    }
  }

  // 3. Listening (mic audio capture)
  if (assistantState === "listening") {
    return { state: "using", variant: "record" };
  }

  // 4. Working (running tools, executing system tasks)
  if (assistantState === "working") {
    return { state: "working", variant: "gyro" };
  }

  // 5. Waiting / Idle (Amigo waiting for user input or ready)
  if (
    assistantState === "idle" ||
    assistantState === "completed" ||
    assistantState === "action_card" ||
    assistantState === "contact_picker"
  ) {
    return { state: "waiting", variant: "default" };
  }

  if (assistantState === "generated_content") {
    return { state: "reasoning", variant: "twins" };
  }

  return { state: "waiting", variant: "default" };
}

export const OrbVisualizer: React.FC<OrbVisualizerProps> = React.memo(({
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
  const isWebSearch =
    activeIntent === "web_search" ||
    activeIntent === "search_web" ||
    activeIntent === "search" ||
    activeIntent === "google_search" ||
    (activePrompt ? /search|google|find online|look up/i.test(activePrompt) : false);

  const orbConfig = getOrbConfig(state, isWebSearch, thinkingOrbStyle, webSearchOrbStyle);

  let orbColor = isDark ? (ORB_COLORS_DARK[state] || "#38bdf8") : (ORB_COLORS_LIGHT[state] || "#0284c7");
  if (isWebSearch && (state === "processing" || state === "working")) {
    orbColor = isDark ? "#00f0ff" : "#0284c7";
  }

  // Adaptive speed per animation state
  const speed = isPaused
    ? 0
    : orbConfig.state === "searching"
    ? 1.4
    : orbConfig.state === "reasoning"
    ? 1.3
    : orbConfig.state === "working"
    ? 1.35
    : orbConfig.state === "using"
    ? 1.15
    : 1.0;

  const coreRef = useRef<HTMLDivElement>(null);
  const audioTargetRef = useRef(0);
  const audioSmoothRef = useRef(0);

  // Live microphone audio reactivity for natural breathing pulse while speaking
  useEffect(() => {
    return audioBus.subscribe((level) => {
      audioTargetRef.current = Math.min(Math.max(level, 0), 1);
    });
  }, []);

  useEffect(() => {
    let raf = 0;
    let lastTime = performance.now();
    const updateAudio = (now: number) => {
      const dt = Math.min((now - lastTime) / 1000, 0.04);
      lastTime = now;
      const targetAudio = state === "listening" ? audioTargetRef.current : 0;
      const timeConst = targetAudio > audioSmoothRef.current ? 0.024 : 0.06;
      const alpha = 1 - Math.exp(-dt / timeConst);
      audioSmoothRef.current += (targetAudio - audioSmoothRef.current) * alpha;

      if (coreRef.current) {
        const scale = 1.0 + audioSmoothRef.current * 0.08;
        coreRef.current.style.transform = `scale(${scale.toFixed(3)}) translateZ(0)`;
      }
      raf = requestAnimationFrame(updateAudio);
    };
    raf = requestAnimationFrame(updateAudio);
    return () => cancelAnimationFrame(raf);
  }, [state]);

  const ariaLabel =
    orbConfig.state === "searching"
      ? "Amigo is searching"
      : orbConfig.state === "reasoning"
      ? "Amigo is thinking"
      : orbConfig.state === "waiting"
      ? "Amigo is waiting"
      : ORB_LABELS[state] || ORB_LABELS.idle;

  return (
    <div
      className={`amigo-orb-stage relative flex items-center justify-center z-0 pointer-events-none transition-all duration-500 overflow-visible ${
        compact ? "compact opacity-0 scale-75" : "opacity-100 scale-100"
      }`}
    >
      {/* Ambient Radiant Glow Backdrop */}
      <div
        className="absolute w-[18rem] h-[18rem] sm:w-[22rem] sm:h-[22rem] rounded-full pointer-events-none transition-all duration-700 ease-out overflow-visible"
        style={{
          background: isDark
            ? `radial-gradient(circle, ${orbColor}55 0%, ${orbColor}28 35%, ${orbColor}0a 60%, transparent 75%)`
            : `radial-gradient(circle, ${orbColor}38 0%, ${orbColor}18 35%, ${orbColor}05 60%, transparent 75%)`,
          filter: "blur(36px)",
          opacity: state === "listening" ? 1.0 : 0.85,
          transform: "translate3d(0, 0, 0)",
        }}
      />
      <div
        ref={coreRef}
        className="amigo-orb-core overflow-visible flex items-center justify-center transition-colors duration-500"
        style={{ color: orbColor }}
      >
        <Orb
          state={orbConfig.state as any}
          variant={orbConfig.variant as any}
          size={compact ? 120 : 210}
          speed={speed}
          paused={isPaused}
          label={ariaLabel}
          className="transition-transform duration-300 pointer-events-none"
        />
      </div>
    </div>
  );
}, (prev, next) =>
  prev.state === next.state &&
  prev.colorTheme === next.colorTheme &&
  prev.isDark === next.isDark &&
  prev.compact === next.compact &&
  prev.isPaused === next.isPaused &&
  prev.thinkingOrbStyle === next.thinkingOrbStyle &&
  prev.webSearchOrbStyle === next.webSearchOrbStyle &&
  prev.activeIntent === next.activeIntent &&
  prev.activePrompt === next.activePrompt
);

export const CanvasVisualizer = OrbVisualizer;
