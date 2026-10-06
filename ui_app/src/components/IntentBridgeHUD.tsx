import React, { useRef, useEffect, useState, useCallback, useId, type RefObject } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import {
  CheckCircle2,
  Loader2,
  AlertCircle,
  WifiOff,
  User,
  Sparkles,
  X,
  Globe,
  Music,
  Terminal,
  Volume2,
  Sun,
  Camera,
  Calendar as CalendarIcon,
  Folder,
  Cpu,
  Mail,
  Calculator,
  FileText,
  Layers,
  Activity,
  Sliders,
  Play,
} from "lucide-react";
import { ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";

export interface AnimatedBeamProps {
  containerRef: RefObject<HTMLElement | null>;
  fromRef: RefObject<HTMLElement | null>;
  toRef: RefObject<HTMLElement | null>;
  curvature?: number;
  reverse?: boolean;
  duration?: number;
  delay?: number;
  pathColor?: string;
  gradientStart?: string;
  gradientStop?: string;
  strokeWidth?: number;
  className?: string;
}

export function AnimatedBeam({
  containerRef,
  fromRef,
  toRef,
  curvature = 0,
  reverse = false,
  duration = 2.4,
  delay = 0,
  pathColor = "rgba(255, 255, 255, 0.12)",
  gradientStart = "#8b5cf6",
  gradientStop = "#ec4899",
  strokeWidth = 2,
  className = "",
}: AnimatedBeamProps) {
  const gradientId = useId();
  const [path, setPath] = useState("");
  const [size, setSize] = useState({ width: 0, height: 0 });
  const prefersReducedMotion = useReducedMotion();

  const measure = useCallback(() => {
    const container = containerRef.current;
    const from = fromRef.current;
    const to = toRef.current;
    if (!container || !from || !to) return;

    const containerRect = container.getBoundingClientRect();
    const fromRect = from.getBoundingClientRect();
    const toRect = to.getBoundingClientRect();

    setSize({ width: containerRect.width, height: containerRect.height });

    const startX = fromRect.left - containerRect.left + fromRect.width / 2;
    const startY = fromRect.top - containerRect.top + fromRect.height / 2;
    const endX = toRect.left - containerRect.left + toRect.width / 2;
    const endY = toRect.top - containerRect.top + toRect.height / 2;

    const controlX = (startX + endX) / 2;
    const controlY = (startY + endY) / 2 - curvature;

    setPath(`M ${startX},${startY} Q ${controlX},${controlY} ${endX},${endY}`);
  }, [containerRef, fromRef, toRef, curvature]);

  useEffect(() => {
    measure();
    const t1 = setTimeout(measure, 50);
    const t2 = setTimeout(measure, 160);
    const t3 = setTimeout(measure, 360);

    const container = containerRef.current;
    let observer: ResizeObserver | null = null;
    if (container) {
      observer = new ResizeObserver(measure);
      observer.observe(container);
      if (fromRef.current) observer.observe(fromRef.current);
      if (toRef.current) observer.observe(toRef.current);
    }

    window.addEventListener("resize", measure);

    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
      if (observer) observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [measure, containerRef, fromRef, toRef]);

  if (!path) return null;

  return (
    <svg
      fill="none"
      width={size.width}
      height={size.height}
      viewBox={`0 0 ${size.width} ${size.height}`}
      aria-hidden="true"
      className={`pointer-events-none absolute top-0 left-0 overflow-visible ${className}`}
    >
      <path
        d={path}
        stroke={pathColor}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeOpacity={0.35}
      />
      <path
        d={path}
        stroke={`url(#${gradientId})`}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
      />
      <defs>
        <motion.linearGradient
          id={gradientId}
          gradientUnits="userSpaceOnUse"
          initial={{ x1: "0%", x2: "0%", y1: "0%", y2: "0%" }}
          animate={
            prefersReducedMotion
              ? { x1: "0%", x2: "100%", y1: "0%", y2: "0%" }
              : {
                  x1: reverse ? ["100%", "-10%"] : ["-10%", "100%"],
                  x2: reverse ? ["110%", "0%"] : ["0%", "110%"],
                  y1: ["0%", "0%"],
                  y2: ["0%", "0%"],
                }
          }
          transition={{
            duration,
            delay,
            repeat: prefersReducedMotion ? 0 : Infinity,
            repeatDelay: 0.35,
            ease: "easeInOut",
          }}
        >
          <stop stopColor={gradientStart} stopOpacity="0" />
          <stop stopColor={gradientStart} />
          <stop offset="35%" stopColor={gradientStop} />
          <stop offset="100%" stopColor={gradientStop} stopOpacity="0" />
        </motion.linearGradient>
      </defs>
    </svg>
  );
}


export const NON_ACTION_INTENTS = new Set([
  "chat",
  "error",
  "clarification",
  "greeting",
  "chitchat",
  "conversation",
  "none",
  "unknown",
]);

// Polite conversational prefixes to strip when evaluating user prompts
const POLITE_PREFIXES = /^(?:hey\s+amigo|amigo|please|could\s+you(?:\s+please)?|can\s+you(?:\s+please)?|would\s+you(?:\s+mind)?|kindly|i\s+want\s+to|i\s+need\s+to|help\s+me(?:\s+to)?|tell\s+me|what\s+is|what's|how\s+is|how's|check)\s+/i;

// Generalized Action Intent Detection
export function isActionIntent(text: string, intent?: string): boolean {
  if (intent !== undefined && intent !== null && intent.trim() !== "") {
    return !NON_ACTION_INTENTS.has(intent.toLowerCase().trim());
  }

  if (!text || !text.trim()) return false;
  let clean = text.toLowerCase().replace(/[_]/g, " ").trim();
  clean = clean.replace(POLITE_PREFIXES, "").trim();

  const actionTriggers = [
    /\b(?:open|launch|start|run|close|kill|terminate|exit|quit)\b/i,
    /\b(?:play|pause|resume|skip|next|prev|previous|stop|mute|unmute)\b/i,
    /\b(?:volume|brightness|sound|screen|display)\b/i,
    /\b(?:weather|forecast|temperature)\b/i,
    /\b(?:time|date|clock)\b/i,
    /\b(?:timer|stopwatch|countdown|alarm|remind|reminder|schedule|calendar)\b/i,
    /\b(?:screenshot|snip|capture|screen\s+vision|take\s+picture)\b/i,
    /\b(?:lock|sleep|restart|reboot|shutdown|recycle\s+bin)\b/i,
    /\b(?:search|google|youtube|browse|look\s+up|find\s+file|open\s+folder|open\s+file)\b/i,
    /\b(?:email|inbox|mail|send\s+mail|send\s+email|send\s+message)\b/i,
    /\b(?:calculate|calc|compute|solve|convert)\b/i,
    /\b(?:system\s+status|cpu|ram|battery|hardware|disk\s+space)\b/i,
    /\b(?:type|press|scroll)\b/i,
    /\b(?:minimize|maximize|snap\s+left|snap\s+right|show\s+desktop)\b/i,
    /\b(?:create|delete|make|write|generate|execute)\b/i,
  ];

  return actionTriggers.some((p) => p.test(clean));
}


// Brand Icons
const YouTubeIcon = () => (
  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none">
    <path
      d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814z"
      fill="#FF0000"
    />
    <path d="M9.545 15.568V8.432L15.818 12l-6.273 3.568z" fill="#FFFFFF" />
  </svg>
);

const GoogleIcon = () => (
  <svg className="w-4 h-4" viewBox="0 0 24 24">
    <path
      d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      fill="#4285F4"
    />
    <path
      d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      fill="#34A853"
    />
    <path
      d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
      fill="#FBBC05"
    />
    <path
      d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
      fill="#EA4335"
    />
  </svg>
);

const SpotifyIcon = () => (
  <svg className="w-4 h-4" viewBox="0 0 24 24">
    <circle cx="12" cy="12" r="12" fill="#1DB954" />
    <path
      d="M17.5 16.3c-.2.3-.5.4-.8.2-2.3-1.4-5.2-1.7-8.6-.9-.3.1-.7-.1-.8-.4-.1-.3.1-.7.4-.8 3.8-.9 7-.5 9.6 1.1.3.2.4.5.2.8zm1.2-2.6c-.2.4-.7.5-1.1.3-2.6-1.6-6.6-2.1-9.7-1.1-.4.1-.9-.1-1-.5-.1-.4.1-.9.5-1 3.6-1.1 8-.5 11 1.3.4.2.5.7.3 1zm.1-2.7c-3.1-1.9-8.3-2-11.3-1.1-.5.1-1-.2-1.1-.7-.1-.5.2-1 .7-1.1 3.5-1.1 9.2-.9 12.8 1.2.4.3.6.9.3 1.3-.3.5-.9.6-1.4.4z"
      fill="#FFFFFF"
    />
  </svg>
);

interface ActionVisualInfo {
  name: string;
  actionVerb: string;
  completedText: string;
  icon: React.ReactNode;
}

function extractDynamicParam(params?: Record<string, any>, prompt?: string): string {
  if (params && typeof params === "object") {
    const candidateKeys = [
      "query", "q", "song", "video", "app_name", "application", "app",
      "city", "location", "target", "task", "action", "command",
      "recipient", "to", "subject", "file", "filename", "path",
      "text", "message", "title", "content", "url"
    ];
    for (const key of candidateKeys) {
      if (typeof params[key] === "string" && params[key].trim()) {
        return params[key].trim();
      }
    }
    for (const [key, val] of Object.entries(params)) {
      if (typeof val === "string" && val.trim() && val.length > 0 && val.length < 100 && !key.startsWith("_")) {
        return val.trim();
      }
    }
  }
  return "";
}

function formatIntentName(raw: string): string {
  if (!raw) return "Agentic Tool";
  return raw
    .replace(/[_\-.]+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function getDynamicToolIcon(intentStr: string, name: string): React.ReactNode {
  const s = (intentStr + " " + name).toLowerCase();
  if (s.includes("youtube")) return <YouTubeIcon />;
  if (s.includes("spotify")) return <SpotifyIcon />;
  if (s.includes("google") || s.includes("search") || s.includes("browse") || s.includes("web")) return <GoogleIcon />;
  if (s.includes("screen") || s.includes("vision") || s.includes("camera") || s.includes("photo") || s.includes("snip")) {
    return <Camera className="w-4 h-4 text-cyan-400" />;
  }
  if (s.includes("weather") || s.includes("forecast") || s.includes("temp")) {
    return <Sun className="w-4 h-4 text-amber-400" />;
  }
  if (s.includes("volume") || s.includes("sound") || s.includes("mute") || s.includes("audio")) {
    return <Volume2 className="w-4 h-4 text-sky-400" />;
  }
  if (s.includes("brightness") || s.includes("display")) {
    return <Sun className="w-4 h-4 text-amber-400" />;
  }
  if (s.includes("music") || s.includes("track") || s.includes("playlist")) {
    return <Music className="w-4 h-4 text-emerald-400" />;
  }
  if (s.includes("timer") || s.includes("calendar") || s.includes("clock") || s.includes("alarm") || s.includes("schedule")) {
    return <CalendarIcon className="w-4 h-4 text-amber-400" />;
  }
  if (s.includes("mail") || s.includes("email") || s.includes("inbox") || s.includes("send")) {
    return <Mail className="w-4 h-4 text-blue-400" />;
  }
  if (s.includes("calc") || s.includes("math") || s.includes("compute")) {
    return <Calculator className="w-4 h-4 text-emerald-400" />;
  }
  if (s.includes("file") || s.includes("folder") || s.includes("doc") || s.includes("note")) {
    return <Folder className="w-4 h-4 text-indigo-400" />;
  }
  if (s.includes("cpu") || s.includes("system") || s.includes("hardware") || s.includes("ram") || s.includes("battery")) {
    return <Cpu className="w-4 h-4 text-purple-400" />;
  }
  if (s.includes("terminal") || s.includes("shell") || s.includes("cmd") || s.includes("bash") || s.includes("run") || s.includes("git")) {
    return <Terminal className="w-4 h-4 text-rose-400" />;
  }
  if (s.includes("setting") || s.includes("config") || s.includes("pref")) {
    return <Sliders className="w-4 h-4 text-violet-400" />;
  }
  return <Layers className="w-4 h-4 text-violet-400" />;
}

function resolveActionInfo(intent?: string, params?: Record<string, any>, prompt?: string): ActionVisualInfo {
  const cleanIntent = (intent || "").toLowerCase().trim();
  const p = params || {};
  const pr = (prompt || "").toLowerCase().trim();
  const paramVal = extractDynamicParam(p, prompt);

  // 1. YouTube
  if (cleanIntent.includes("youtube") || pr.includes("youtube")) {
    const q = paramVal || "YouTube Video";
    return {
      name: "YouTube",
      actionVerb: `Streaming "${q}"...`,
      completedText: `Playing on YouTube`,
      icon: <YouTubeIcon />,
    };
  }

  // 2. Spotify / Music
  if (cleanIntent.includes("spotify") || pr.includes("spotify")) {
    return {
      name: "Spotify",
      actionVerb: paramVal ? `Playing "${paramVal}"...` : "Controlling Audio Playback...",
      completedText: "Playback Updated",
      icon: <SpotifyIcon />,
    };
  }

  // 3. Web Search / Google
  if (cleanIntent.includes("search") || cleanIntent.includes("google") || cleanIntent === "web_search" || pr.includes("google")) {
    const q = paramVal || prompt || "Web Knowledge";
    return {
      name: "Google Search",
      actionVerb: `Searching "${q}"...`,
      completedText: `Search Results Retrieved`,
      icon: <GoogleIcon />,
    };
  }

  // 4. Screen Vision
  if (cleanIntent.includes("screen") || cleanIntent.includes("vision") || pr.includes("screenshot")) {
    return {
      name: "Screen Vision",
      actionVerb: "Analyzing Screen Context...",
      completedText: "Screen Analyzed",
      icon: <Camera className="w-4 h-4 text-cyan-400" />,
    };
  }

  // 5. Weather
  if (cleanIntent.includes("weather") || pr.includes("weather")) {
    const loc = paramVal || "Local Area";
    return {
      name: "Weather Radar",
      actionVerb: `Querying Weather for ${loc}...`,
      completedText: `Weather Data Synchronized`,
      icon: <Sun className="w-4 h-4 text-amber-400" />,
    };
  }

  // 6. Volume
  if (cleanIntent.includes("volume") || pr.includes("volume") || cleanIntent.includes("audio")) {
    return {
      name: "System Audio",
      actionVerb: "Adjusting System Volume...",
      completedText: "Volume Adjusted",
      icon: <Volume2 className="w-4 h-4 text-sky-400" />,
    };
  }

  // 7. Brightness
  if (cleanIntent.includes("brightness") || pr.includes("brightness")) {
    return {
      name: "Display",
      actionVerb: "Adjusting Display Brightness...",
      completedText: "Brightness Updated",
      icon: <Sun className="w-4 h-4 text-amber-400" />,
    };
  }

  // 8. Timer & Calendar
  if (cleanIntent.includes("timer") || cleanIntent.includes("alarm") || cleanIntent.includes("calendar") || pr.includes("timer")) {
    const label = cleanIntent.includes("timer") ? "Timer" : cleanIntent.includes("alarm") ? "Alarm" : "Calendar";
    return {
      name: `${label} Engine`,
      actionVerb: `Scheduling ${label}...`,
      completedText: `${label} Active`,
      icon: <CalendarIcon className="w-4 h-4 text-amber-400" />,
    };
  }

  // 9. App Launcher
  if (cleanIntent.includes("open") || cleanIntent.includes("launch") || pr.startsWith("open ") || pr.startsWith("launch ")) {
    const appName = paramVal || (p.app_name || p.application) || "Application";
    return {
      name: appName,
      actionVerb: `Launching ${appName}...`,
      completedText: `${appName} Running`,
      icon: <Folder className="w-4 h-4 text-indigo-400" />,
    };
  }

  // 10. Dynamic Fallback for ANY arbitrary intent / tool
  const dynamicName = formatIntentName(intent || (paramVal ? "Action Tool" : "Agentic Tool"));
  const dynamicIcon = getDynamicToolIcon(cleanIntent, dynamicName);
  const actionVerb = paramVal
    ? `Executing ${dynamicName} ("${paramVal}")...`
    : `Executing ${dynamicName}...`;
  const completedText = `${dynamicName} Completed`;

  return {
    name: dynamicName,
    actionVerb,
    completedText,
    icon: dynamicIcon,
  };
}

export interface IntentBridgeHUDProps {
  prompt: string;
  isDark: boolean;
  colorTheme?: ColorTheme;
  statusText?: string;
  isCompleted?: boolean;
  status?: string;
  intent?: string;
  params?: Record<string, any>;
  historyCount?: number;
  onDismiss?: () => void;
}

/**
 * High-tech Intent Bridge Powered by AnimatedBeam.
 * Visualizes the dynamic bridge from User -> Amigo AI Core Logo -> Resolved Action/Service.
 */
export const IntentBridgeHUD: React.FC<IntentBridgeHUDProps> = ({
  prompt,
  isDark,
  colorTheme = "violet",
  statusText,
  isCompleted = false,
  status,
  intent,
  params,
  onDismiss,
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const actionInfo = resolveActionInfo(intent, params, prompt);

  const containerRef = useRef<HTMLDivElement>(null);
  const userNodeRef = useRef<HTMLDivElement>(null);
  const amigoNodeRef = useRef<HTMLDivElement>(null);
  const targetNodeRef = useRef<HTMLDivElement>(null);

  const isOffline = status === "offline" || statusText?.toLowerCase().includes("offline");
  const isFailed = status === "failed" || statusText?.toLowerCase().includes("failed");
  const isSuccess = isCompleted && !isOffline && !isFailed;

  // Auto-dismiss completed HUD bridge smoothly after 7 seconds
  useEffect(() => {
    if (isCompleted && onDismiss) {
      const timer = setTimeout(() => {
        onDismiss();
      }, 7000);
      return () => clearTimeout(timer);
    }
  }, [isCompleted, onDismiss]);

  const beamGradientStart = theme.primary;
  const beamGradientStop = isSuccess
    ? "#10b981"
    : isFailed
    ? "#f59e0b"
    : (theme.accent || "#ec4899");

  return (
    <motion.div
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -6, scale: 0.98 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className="relative w-full max-w-xs sm:max-w-sm mx-auto my-1 pointer-events-auto bg-transparent border-0 shadow-none"
    >
      <div
        ref={containerRef}
        className="relative w-full py-1 bg-transparent border-0 shadow-none overflow-visible"
      >
        {/* 3 Connected Interactive Nodes via AnimatedBeam */}
        <div className="relative py-1 flex items-center justify-between px-4 sm:px-8">
          {/* Node 1: User / Voice Prompt Node */}
          <div className="flex flex-col items-center text-center z-10">
            <div
              ref={userNodeRef}
              className={`relative w-9 h-9 rounded-xl border flex items-center justify-center shadow-md transition-transform hover:scale-105 ${
                isDark
                  ? "bg-slate-900/60 border-white/10 text-slate-200"
                  : "bg-white/60 border-slate-200 text-slate-800"
              }`}
            >
              <User className="w-4 h-4 opacity-90" />
            </div>
            <span className="text-[9px] font-medium opacity-60 mt-1 max-w-[70px] truncate">
              {prompt || "User"}
            </span>
          </div>

          {/* Node 2: Amigo AI Core Logo Node (Center) */}
          <div className="flex flex-col items-center text-center z-10">
            <div
              ref={amigoNodeRef}
              className="relative w-10 h-10 rounded-xl flex items-center justify-center shadow-lg transition-transform hover:scale-105"
              style={{
                background: theme.gradient,
                boxShadow: `0 0 20px -2px ${theme.glow}`,
              }}
            >
              {/* Pulsing Aura Ring */}
              <span
                className="absolute inset-0 rounded-xl border animate-pulse opacity-60 pointer-events-none"
                style={{ borderColor: theme.accent || "#fff" }}
              />
              <Sparkles className="w-4.5 h-4.5 text-white drop-shadow" />
            </div>
            <span className="text-[10px] font-semibold mt-1 text-slate-900 dark:text-white">
              Amigo Core
            </span>
          </div>

          {/* Node 3: Target Action / Service Node */}
          <div className="flex flex-col items-center text-center z-10">
            <div
              ref={targetNodeRef}
              className={`relative w-9 h-9 rounded-xl border flex items-center justify-center shadow-md transition-transform hover:scale-105 ${
                isDark
                  ? "bg-slate-900/60 border-white/10 text-slate-200"
                  : "bg-white/60 border-slate-200 text-slate-800"
              }`}
              style={
                isSuccess
                  ? { borderColor: "#10b981", boxShadow: "0 0 12px -3px #10b98160" }
                  : {}
              }
            >
              {actionInfo.icon}
            </div>
            <span className="text-[9px] font-medium opacity-60 mt-1 max-w-[70px] truncate">
              {actionInfo.name}
            </span>
          </div>

          {/* Beam 1: User Node -> Amigo Core Logo */}
          <AnimatedBeam
            containerRef={containerRef}
            fromRef={userNodeRef}
            toRef={amigoNodeRef}
            curvature={0}
            duration={2.2}
            pathColor={isDark ? "rgba(255, 255, 255, 0.12)" : "rgba(0, 0, 0, 0.1)"}
            gradientStart={theme.secondary || "#38bdf8"}
            gradientStop={theme.primary}
            strokeWidth={2}
          />

          {/* Beam 2: Amigo Core Logo -> Target Action Node */}
          <AnimatedBeam
            containerRef={containerRef}
            fromRef={amigoNodeRef}
            toRef={targetNodeRef}
            curvature={0}
            duration={2.2}
            delay={0.25}
            pathColor={isDark ? "rgba(255, 255, 255, 0.12)" : "rgba(0, 0, 0, 0.1)"}
            gradientStart={beamGradientStart}
            gradientStop={beamGradientStop}
            strokeWidth={2}
          />
        </div>

        {/* Seamless Status Label */}
        <div className="flex items-center justify-center gap-1.5 mt-1 text-[10px] font-medium">
          {isSuccess ? (
            <CheckCircle2 className="w-3 h-3 text-emerald-400 flex-shrink-0" />
          ) : isFailed ? (
            <AlertCircle className="w-3 h-3 text-amber-400 flex-shrink-0" />
          ) : (
            <span
              className="w-1 h-1 rounded-full animate-ping flex-shrink-0"
              style={{ backgroundColor: theme.primary }}
            />
          )}
          <span className="opacity-75">
            {statusText || (isSuccess ? actionInfo.completedText : actionInfo.actionVerb)}
          </span>
        </div>
      </div>
    </motion.div>
  );
};

export default IntentBridgeHUD;
