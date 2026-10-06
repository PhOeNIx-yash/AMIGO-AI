import React, { useRef, useEffect, useId } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  CheckCircle2,
  AlertCircle,
  Sparkles,
  X,
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
  Layers,
  Sliders,
} from "lucide-react";
import { ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";


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

  // Fallback: extract meaningful target directly from the user's prompt
  if (prompt && prompt.trim()) {
    const clean = prompt.trim().replace(/[.!?]+$/, "");
    const match = clean.match(/^(?:open|launch|start|run|play|search|find|show|check|query)\s+(.+)$/i);
    if (match && match[1]) {
      return match[1].trim();
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
  if (s.includes("antigravity")) return <Cpu className="w-4 h-4 text-cyan-400" />;
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
    let appName = paramVal || (p.app_name || p.application);
    if (!appName || appName === "Application") {
      const match = pr.match(/^(?:open|launch|start|run)\s+(.+)$/i);
      appName = match ? formatIntentName(match[1]) : "Application";
    } else {
      appName = formatIntentName(appName);
    }

    return {
      name: appName,
      actionVerb: `Launching ${appName}...`,
      completedText: `${appName} Running`,
      icon: getDynamicToolIcon(cleanIntent, appName),
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
 * High-tech 2-Node Dynamic Action Capsule Powered by AnimatedBeam.
 * Visualizes the direct, focused connection: Amigo Core ──laser beam──> Target Action.
 * Zero background boxes, seamless, compact, zero overlap with central orb.
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
  const laserGradId = useId();

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
      initial={{ opacity: 0, y: 8, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 4, scale: 0.96 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className="relative w-auto max-w-[240px] sm:max-w-[260px] mx-auto pointer-events-auto select-none bg-transparent border-0 shadow-none"
    >
      <div className="relative w-full py-1 bg-transparent border-0 shadow-none overflow-visible flex flex-col items-center group">
        {/* 2 Connected Interactive Nodes via GPU-accelerated Laser Stream */}
        <div className="relative flex items-center justify-between w-full px-2 py-1">
          {/* Node 1: Amigo AI Core Logo Node */}
          <div className="flex flex-col items-center text-center z-10">
            <div
              className="relative w-9 h-9 rounded-xl flex items-center justify-center shadow-lg transition-transform hover:scale-105"
              style={{
                background: theme.gradient,
                boxShadow: `0 0 16px -2px ${theme.glow}`,
              }}
            >
              {/* Pulsing Aura Ring */}
              <span
                className="absolute inset-0 rounded-xl border animate-pulse opacity-60 pointer-events-none"
                style={{ borderColor: theme.accent || "#fff" }}
              />
              <Sparkles className="w-4 h-4 text-white drop-shadow" />
            </div>
            <span className="text-[10px] font-semibold mt-1 text-slate-800 dark:text-white">
              Amigo Core
            </span>
          </div>

          {/* GPU Hardware-Accelerated 120 FPS Laser Beam Stream */}
          <div className="relative flex-1 flex items-center justify-center mx-2 h-6 select-none pointer-events-none">
            <svg className="w-full h-4 overflow-visible" fill="none">
              <defs>
                <linearGradient id={laserGradId} x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor={beamGradientStart} stopOpacity="0.3" />
                  <stop offset="50%" stopColor={beamGradientStop} stopOpacity="1" />
                  <stop offset="100%" stopColor={beamGradientStop} stopOpacity="0.4" />
                </linearGradient>
              </defs>
              {/* Background guide track */}
              <line
                x1="2"
                y1="8"
                x2="100%"
                y2="8"
                stroke={isDark ? "rgba(255, 255, 255, 0.12)" : "rgba(0, 0, 0, 0.10)"}
                strokeWidth="1.5"
                strokeLinecap="round"
              />
              {/* High-speed GPU compositor laser flow */}
              <line
                x1="2"
                y1="8"
                x2="100%"
                y2="8"
                stroke={isSuccess ? "#10b981" : `url(#${laserGradId})`}
                strokeWidth={isSuccess ? "2" : "2.5"}
                strokeLinecap="round"
                className={isSuccess ? "" : "laser-beam-stream"}
                style={isSuccess ? { filter: "drop-shadow(0 0 6px #10b981)" } : undefined}
              />
            </svg>
          </div>

          {/* Node 2: Target Action / Service Node */}
          <div className="flex flex-col items-center text-center z-10">
            <div
              className={`relative w-9 h-9 rounded-xl border flex items-center justify-center shadow-md transition-all hover:scale-105 ${
                isDark
                  ? "bg-slate-900/60 border-white/10 text-slate-200"
                  : "bg-white/60 border-slate-200 text-slate-800"
              }`}
              style={
                isSuccess
                  ? { borderColor: "#10b981", boxShadow: "0 0 14px -2px #10b98160" }
                  : isFailed
                  ? { borderColor: "#f59e0b", boxShadow: "0 0 14px -2px #f59e0b60" }
                  : {}
              }
            >
              {actionInfo.icon}
            </div>
            <span className="text-[10px] font-medium opacity-75 mt-1 max-w-[85px] truncate">
              {actionInfo.name}
            </span>
          </div>
        </div>

        {/* Seamless Status Label */}
        <div className="flex items-center justify-center gap-1.5 mt-1 text-[10px] font-medium">
          {isSuccess ? (
            <CheckCircle2 className="w-3 h-3 text-emerald-400 flex-shrink-0" />
          ) : isFailed ? (
            <AlertCircle className="w-3 h-3 text-amber-400 flex-shrink-0" />
          ) : (
            <span
              className="w-1.5 h-1.5 rounded-full animate-ping flex-shrink-0"
              style={{ backgroundColor: theme.primary }}
            />
          )}
          <span className="opacity-75 text-slate-700 dark:text-slate-300">
            {statusText || (isSuccess ? actionInfo.completedText : actionInfo.actionVerb)}
          </span>
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              className="opacity-0 group-hover:opacity-60 hover:!opacity-100 transition-opacity ml-1 p-0.5 rounded text-slate-400 hover:text-slate-200"
              title="Dismiss"
            >
              <X className="w-2.5 h-2.5" />
            </button>
          )}
        </div>
      </div>
    </motion.div>
  );
};

export default IntentBridgeHUD;
