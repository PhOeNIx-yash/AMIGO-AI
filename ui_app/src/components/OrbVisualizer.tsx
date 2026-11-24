import React, { useEffect, useRef, useMemo } from "react";
import { Orb, type OrbState, type OrbVariant } from "@yogesharc/thinking-orbs";
import { torus, cube } from "@yogesharc/thinking-orbs/shapes";
import { mesh, dashes, squares } from "@yogesharc/thinking-orbs/renders";
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
  { id: "orbits", name: "Cosmic Orbits", orbState: "working", desc: "3D gyroscopic particle orbits" },
  { id: "rubik", name: "Puzzle Matrix", orbState: "reasoning", desc: "3D geometric reasoning cube matrix" },
  { id: "morph", name: "Morphing Core", orbState: "compacting", desc: "3D topological squeeze-and-fuse geometry" },
  { id: "ring", name: "Harmonic Rings", orbState: "working", desc: "3D quantum torus concentric orbital ring" },
  { id: "web", name: "Neural Web", orbState: "reasoning", desc: "3D synaptic constellation neural network mesh" },
  { id: "globe", name: "Radar Globe", orbState: "searching", desc: "3D scanning coordinate sphere with radar sweep" },
];

export const WEB_SEARCH_ORB_PRESETS: {
  id: ThinkingOrbStyle;
  name: string;
  orbState: OrbState;
  desc: string;
}[] = [
  { id: "globe", name: "Radar Globe", orbState: "searching", desc: "3D scanning coordinate sphere with radar sweep" },
  { id: "web", name: "Neural Web", orbState: "searching", desc: "3D synaptic constellation with packet routing" },
  { id: "orbits", name: "Satellite Orbits", orbState: "working", desc: "3D planetary orbital tracking bands" },
  { id: "rubik", name: "Index Matrix", orbState: "reasoning", desc: "3D segmented search index matrix" },
];

export const THINKING_ORB_PRESETS = AMIGO_ORB_PRESETS;

export const STYLE_TO_ORB_STATE: Record<ThinkingOrbStyle, OrbState> = {
  globe: "searching",
  orbits: "working",
  web: "reasoning",
  morph: "compacting",
  ring: "working",
  rubik: "reasoning",
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

interface OrbRenderConfig {
  state: OrbState;
  variant?: OrbVariant<OrbState>;
  shape?: any;
  render?: any;
  shapeId: string;
  renderId: string;
  speed: number;
  tilt?: number;
}

/**
 * Computes full 3D visual configuration for the thinking orb based on
 * assistant state, web search status, and selected visual style preset.
 */
function getOrbRenderConfig(
  state: AssistantState,
  isWebSearch: boolean,
  thinkingOrbStyle: ThinkingOrbStyle,
  webSearchOrbStyle: ThinkingOrbStyle
): OrbRenderConfig {
  // 1. Listening: audio pulse animation with recording indicator
  if (state === "listening") {
    return {
      state: "using",
      variant: "record",
      shapeId: "sphere",
      renderId: "dots",
      speed: 1.15,
      tilt: 20,
    };
  }

  // 2. Web search mode (during processing or working)
  if (isWebSearch && (state === "processing" || state === "working")) {
    switch (webSearchOrbStyle) {
      case "web":
        return {
          state: "searching",
          render: mesh,
          shapeId: "sphere",
          renderId: "mesh",
          speed: 1.4,
          tilt: 20,
        };
      case "orbits":
        return {
          state: "working",
          variant: "gyro",
          render: dashes,
          shapeId: "sphere",
          renderId: "dashes",
          speed: 1.45,
          tilt: 25,
        };
      case "rubik":
        return {
          state: "reasoning",
          variant: "twins",
          shape: cube,
          shapeId: "cube",
          renderId: "dots",
          speed: 1.35,
          tilt: 20,
        };
      case "globe":
      default:
        return {
          state: "searching",
          variant: "lighthouse",
          shapeId: "sphere",
          renderId: "dots",
          speed: 1.5,
          tilt: 20,
        };
    }
  }

  // 3. Processing (thinking / reasoning)
  if (state === "processing") {
    switch (thinkingOrbStyle) {
      case "globe":
        return {
          state: "searching",
          variant: "lighthouse",
          shapeId: "sphere",
          renderId: "dots",
          speed: 1.45,
          tilt: 20,
        };
      case "web":
        return {
          state: "reasoning",
          variant: "twins",
          render: mesh,
          shapeId: "sphere",
          renderId: "mesh",
          speed: 1.35,
          tilt: 20,
        };
      case "rubik":
        return {
          state: "reasoning",
          variant: "twins",
          shape: cube,
          shapeId: "cube",
          renderId: "dots",
          speed: 1.35,
          tilt: 20,
        };
      case "ring":
        return {
          state: "working",
          variant: "gyro",
          shape: torus,
          shapeId: "torus",
          renderId: "dots",
          speed: 1.4,
          tilt: 35,
        };
      case "morph":
        return {
          state: "compacting",
          variant: "squeeze",
          shapeId: "sphere",
          renderId: "dots",
          speed: 1.35,
          tilt: 20,
        };
      case "orbits":
      default:
        return {
          state: "reasoning",
          variant: "twins",
          shapeId: "sphere",
          renderId: "dots",
          speed: 1.35,
          tilt: 25,
        };
    }
  }

  // 4. Working (active task execution in background)
  if (state === "working") {
    switch (thinkingOrbStyle) {
      case "ring":
        return {
          state: "working",
          variant: "gyro",
          shape: torus,
          shapeId: "torus",
          renderId: "dots",
          speed: 1.5,
          tilt: 35,
        };
      case "rubik":
        return {
          state: "reasoning",
          variant: "twins",
          shape: cube,
          shapeId: "cube",
          renderId: "dots",
          speed: 1.45,
          tilt: 20,
        };
      case "web":
        return {
          state: "reasoning",
          variant: "twins",
          render: mesh,
          shapeId: "sphere",
          renderId: "mesh",
          speed: 1.4,
          tilt: 20,
        };
      case "morph":
        return {
          state: "compacting",
          variant: "fuse",
          shapeId: "sphere",
          renderId: "dots",
          speed: 1.35,
          tilt: 20,
        };
      case "globe":
        return {
          state: "searching",
          variant: "lighthouse",
          shapeId: "sphere",
          renderId: "dots",
          speed: 1.45,
          tilt: 20,
        };
      case "orbits":
      default:
        return {
          state: "working",
          variant: "gyro",
          shapeId: "sphere",
          renderId: "dots",
          speed: 1.5,
          tilt: 25,
        };
    }
  }

  // 5. Action card: user prompt/interaction needed
  if (state === "action_card") {
    return {
      state: "using",
      variant: "tap",
      shapeId: "sphere",
      renderId: "dots",
      speed: 1.15,
      tilt: 20,
    };
  }

  // 6. Contact picker / disambiguation: scanning coordinate spots
  if (state === "contact_picker") {
    return {
      state: "searching",
      variant: "default",
      shapeId: "sphere",
      renderId: "dots",
      speed: 1.2,
      tilt: 20,
    };
  }

  // 7. Generated content: typing characters / review mode
  if (state === "generated_content") {
    return {
      state: "using",
      variant: "type",
      shapeId: "sphere",
      renderId: "dots",
      speed: 1.2,
      tilt: 20,
    };
  }

  // 8. Completed task: comet spiraling smoothly around the orb
  if (state === "completed") {
    return {
      state: "waiting",
      variant: "default",
      shapeId: "sphere",
      renderId: "dots",
      speed: 1.25,
      tilt: 20,
    };
  }

  // 9. Idle (default standby): reflects the user's chosen aesthetic preset!
  // This guarantees each style has an immediate, unique, and visible 3D animation even at rest.
  switch (thinkingOrbStyle) {
    case "rubik":
      return {
        state: "reasoning",
        variant: "twins",
        shape: cube,
        shapeId: "cube",
        renderId: "dots",
        speed: 1.0,
        tilt: 20,
      };
    case "ring":
      return {
        state: "working",
        variant: "gyro",
        shape: torus,
        shapeId: "torus",
        renderId: "dots",
        speed: 1.05,
        tilt: 35,
      };
    case "web":
      return {
        state: "reasoning",
        variant: "default",
        render: mesh,
        shapeId: "sphere",
        renderId: "mesh",
        speed: 1.0,
        tilt: 20,
      };
    case "morph":
      return {
        state: "compacting",
        variant: "squeeze",
        shapeId: "sphere",
        renderId: "dots",
        speed: 0.95,
        tilt: 20,
      };
    case "globe":
      return {
        state: "searching",
        variant: "lighthouse",
        shapeId: "sphere",
        renderId: "dots",
        speed: 1.0,
        tilt: 20,
      };
    case "orbits":
    default:
      return {
        state: "working",
        variant: "gyro",
        shapeId: "sphere",
        renderId: "dots",
        speed: 1.0,
        tilt: 25,
      };
  }
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
    activeIntent === "show_images" ||
    state === ("searching" as any) ||
    (Boolean(activePrompt) &&
      /search|google|image|picture|find|look up|who is|what is|browse|web/i.test(activePrompt!));

  let orbColor = isDark ? (ORB_COLORS_DARK[state] || "#38bdf8") : (ORB_COLORS_LIGHT[state] || "#0284c7");
  if (isWebSearch && (state === "processing" || state === "working")) {
    orbColor = isDark ? "#00f0ff" : "#0284c7";
  }

  const orbConfig = useMemo(
    () => getOrbRenderConfig(state, isWebSearch, thinkingOrbStyle, webSearchOrbStyle),
    [state, isWebSearch, thinkingOrbStyle, webSearchOrbStyle]
  );

  const coreRef = useRef<HTMLDivElement>(null);
  const glowRef = useRef<HTMLDivElement>(null);
  const audioTargetRef = useRef(0);
  const audioSmoothRef = useRef(0);

  // Live microphone audio reactivity for natural breathing pulse
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

      // Organic subtle breathing cycle when in idle or listening
      const breathing = Math.sin(now / 1200) * 0.02;
      const scale = 1.0 + audioSmoothRef.current * 0.12 + breathing;
      const glowScale = 1.0 + audioSmoothRef.current * 0.2 + breathing * 1.5;

      if (coreRef.current) {
        coreRef.current.style.transform = `scale(${scale.toFixed(3)}) translateZ(0)`;
      }
      if (glowRef.current) {
        glowRef.current.style.transform = `scale(${glowScale.toFixed(3)}) translate3d(0, 0, 0)`;
      }
      raf = requestAnimationFrame(updateAudio);
    };
    raf = requestAnimationFrame(updateAudio);
    return () => cancelAnimationFrame(raf);
  }, [state]);

  const ariaLabel =
    isWebSearch && (state === "processing" || state === "working")
      ? "Amigo is searching the web"
      : ORB_LABELS[state] || ORB_LABELS.idle;

  // Remount key guarantees immediate recreation whenever geometry, rendering mode, or state alters
  const orbKey = `${orbConfig.state}-${orbConfig.variant || "default"}-${orbConfig.shapeId}-${orbConfig.renderId}-${state}`;

  return (
    <div
      className={`amigo-orb-stage relative flex items-center justify-center z-0 pointer-events-none transition-all duration-500 overflow-visible ${
        compact ? "compact opacity-0 scale-75" : "opacity-100 scale-100"
      }`}
    >
      {/* Ambient Radiant Glow Backdrop */}
      <div
        ref={glowRef}
        className="absolute w-[18rem] h-[18rem] sm:w-[22rem] sm:h-[22rem] rounded-full pointer-events-none transition-all duration-700 ease-out overflow-visible"
        style={{
          background: isDark
            ? `radial-gradient(circle, ${orbColor}55 0%, ${orbColor}28 35%, ${orbColor}0a 60%, transparent 75%)`
            : `radial-gradient(circle, ${orbColor}38 0%, ${orbColor}18 35%, ${orbColor}05 60%, transparent 75%)`,
          filter: "blur(36px)",
          opacity: state === "listening" ? 1.0 : 0.85,
        }}
      />
      <div
        ref={coreRef}
        className="amigo-orb-core overflow-visible flex items-center justify-center transition-colors duration-500"
        style={{ color: orbColor }}
      >
        <Orb
          key={orbKey}
          state={orbConfig.state as any}
          variant={orbConfig.variant}
          shape={orbConfig.shape}
          render={orbConfig.render}
          tilt={orbConfig.tilt}
          size={compact ? 120 : 210}
          speed={orbConfig.speed}
          paused={Boolean(isPaused)}
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
