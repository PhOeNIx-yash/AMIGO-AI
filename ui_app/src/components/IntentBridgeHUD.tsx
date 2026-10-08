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
  Search,
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

// Precompiled action triggers at module level (not re-allocated per render/call)
const ACTION_TRIGGERS = [
  /\b(?:open|launch)\s+([a-z0-9_.-]+)/i,
  /\b(?:play|stream|listen\s+to)\s+/i,
  /\b(?:pause|resume|skip|mute|unmute)\b/i,
  /\b(?:set|start)\s+(?:a\s+)?(?:timer|alarm|stopwatch|countdown)\b/i,
  /\b(?:turn\s+up|turn\s+down|adjust)\s+(?:volume|brightness)\b/i,
  /\b(?:take\s+a\s+)?(?:screenshot|snip|screen\s+capture)\b/i,
  /\b(?:weather\s+in|forecast\s+for|check\s+weather)\b/i,
  /\b(?:search\s+for|google\s+for|browse\s+for|look\s+up)\b/i,
  /\b(?:send\s+(?:an?\s+)?(?:email|mail|message))\b/i,
  /\b(?:find\s+file|open\s+folder|open\s+file)\b/i,
  /\b(?:lock|sleep|restart|reboot|shutdown)\s+(?:pc|computer|system|screen)\b/i,
  /\b(?:calculate|compute|solve)\s+[0-9]/i,
];

// Generalized Action Intent Detection
export function isActionIntent(text: string, intent?: string): boolean {
  if (intent !== undefined && intent !== null && intent.trim() !== "") {
    return !NON_ACTION_INTENTS.has(intent.toLowerCase().trim());
  }

  if (!text || !text.trim()) return false;
  let clean = text.toLowerCase().replace(/[_]/g, " ").trim();
  clean = clean.replace(POLITE_PREFIXES, "").trim();

  return ACTION_TRIGGERS.some((p) => p.test(clean));
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

const SENSITIVE_KEY_PATTERN = /(?:key|token|auth|secret|pass|cred|bearer|hash|salt|jwt|session|cookie|private)/i;

function extractDynamicParam(params?: Record<string, any>, prompt?: string): string {
  if (params && typeof params === "object") {
    const candidateKeys = [
      "query", "q", "song", "video", "app_name", "application", "app",
      "city", "location", "target", "task", "action", "command",
      "recipient", "to", "subject", "file", "filename", "path",
      "title", "url", "text", "message"
    ];
    for (const key of candidateKeys) {
      if (SENSITIVE_KEY_PATTERN.test(key)) continue;
      const val = params[key];
      if (typeof val === "string" && val.trim()) {
        const trimmed = val.trim();
        if (trimmed.length > 36) {
          return trimmed.slice(0, 34) + "...";
        }
        return trimmed;
      }
    }
    for (const [key, val] of Object.entries(params)) {
      if (SENSITIVE_KEY_PATTERN.test(key) || key.startsWith("_")) continue;
      if (typeof val === "string" && val.trim()) {
        const trimmed = val.trim();
        if (trimmed.length > 36) {
          return trimmed.slice(0, 34) + "...";
        }
        return trimmed;
      }
    }
  }

  // Fallback: extract target directly from prompt
  if (prompt && prompt.trim()) {
    const clean = prompt.trim().replace(/[.!?]+$/, "");
    const match = clean.match(/^(?:open|launch|start|run|play|search\s+for|find)\s+(.+)$/i);
    if (match && match[1]) {
      const target = match[1].trim();
      return target.length > 36 ? target.slice(0, 34) + "..." : target;
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

// Word-boundary exact matching for icons so substrings like "program" don't match "ram"
function getDynamicToolIcon(intentStr: string, name: string): React.ReactNode {
  const s = (intentStr + " " + name).toLowerCase();
  if (/\bantigravity\b/.test(s)) return <Cpu className="w-4 h-4 text-cyan-400" />;
  if (/\byoutube\b/.test(s)) return <YouTubeIcon />;
  if (/\bspotify\b/.test(s)) return <SpotifyIcon />;
  if (/\b(google|web_search|browse)\b/.test(s)) return <GoogleIcon />;
  if (/\b(screen|vision|camera|photo|snip)\b/.test(s)) return <Camera className="w-4 h-4 text-cyan-400" />;
  if (/\b(weather|forecast)\b/.test(s) || /\btemp\b/.test(s)) return <Sun className="w-4 h-4 text-amber-400" />;
  if (/\b(volume|sound|mute|audio)\b/.test(s)) return <Volume2 className="w-4 h-4 text-sky-400" />;
  if (/\b(brightness|display)\b/.test(s)) return <Sun className="w-4 h-4 text-amber-400" />;
  if (/\b(music|track|playlist)\b/.test(s)) return <Music className="w-4 h-4 text-emerald-400" />;
  if (/\b(timer|stopwatch|clock|alarm|calendar|schedule)\b/.test(s)) return <CalendarIcon className="w-4 h-4 text-amber-400" />;
  if (/\b(mail|email|inbox)\b/.test(s)) return <Mail className="w-4 h-4 text-blue-400" />;
  if (/\b(calc|math|compute)\b/.test(s)) return <Calculator className="w-4 h-4 text-emerald-400" />;
  if (/\b(file|folder|directory|doc|notes)\b/.test(s)) return <Folder className="w-4 h-4 text-indigo-400" />;
  if (/\b(cpu|hardware|battery)\b/.test(s) || /\bram\b/.test(s)) return <Cpu className="w-4 h-4 text-purple-400" />;
  if (/\b(terminal|shell|cmd|bash)\b/.test(s) || /\b(run|git)\b/.test(s)) return <Terminal className="w-4 h-4 text-rose-400" />;
  if (/\b(settings|config|preferences)\b/.test(s)) return <Sliders className="w-4 h-4 text-violet-400" />;
  return <Layers className="w-4 h-4 text-violet-400" />;
}

function resolveActionInfo(intent?: string, params?: Record<string, any>, prompt?: string): ActionVisualInfo {
  const cleanIntent = (intent || "").toLowerCase().trim();
  const p = params || {};
  const pr = (prompt || "").toLowerCase().trim();
  const paramVal = extractDynamicParam(p, prompt);

  // File Search / Finder (check before generic search)
  if (cleanIntent.includes("file") || cleanIntent.includes("folder") || (pr.includes("file") && cleanIntent === "find_files")) {
    const q = paramVal || "Documents";
    return {
      name: "File Search",
      actionVerb: `Searching files for "${q}"...`,
      completedText: `File Match Located`,
      icon: <Folder className="w-4 h-4 text-indigo-400" />,
    };
  }

  // YouTube — only stream if intent is YouTube or prompt specifically asks to play/watch
  if (cleanIntent.includes("youtube") || (pr.includes("youtube") && /\b(play|watch|stream|listen)\b/.test(pr))) {
    const q = paramVal || "YouTube Video";
    return {
      name: "YouTube",
      actionVerb: `Streaming "${q}"...`,
      completedText: `Playing on YouTube`,
      icon: <YouTubeIcon />,
    };
  }

  // Spotify / Music
  if (cleanIntent.includes("spotify") || (cleanIntent.includes("music") && !cleanIntent.includes("search"))) {
    return {
      name: "Spotify",
      actionVerb: paramVal ? `Playing "${paramVal}"...` : "Controlling Audio Playback...",
      completedText: "Playback Updated",
      icon: <SpotifyIcon />,
    };
  }

  // Web Search / Google (strict intent check so "summarize" doesn't trigger search)
  if (cleanIntent === "google_search" || cleanIntent === "web_search" || cleanIntent === "search") {
    const q = paramVal || prompt || "Web Knowledge";
    return {
      name: "Google Search",
      actionVerb: `Searching "${q}"...`,
      completedText: `Search Results Retrieved`,
      icon: <GoogleIcon />,
    };
  }

  // Screen Vision
  if (cleanIntent.includes("screen") || cleanIntent.includes("vision") || pr.includes("screenshot")) {
    return {
      name: "Screen Vision",
      actionVerb: "Analyzing Screen Context...",
      completedText: "Screen Analyzed",
      icon: <Camera className="w-4 h-4 text-cyan-400" />,
    };
  }

  // Weather
  if (cleanIntent.includes("weather") || (pr.includes("weather") && !cleanIntent)) {
    const loc = paramVal || "Local Area";
    return {
      name: "Weather Radar",
      actionVerb: `Querying Weather for ${loc}...`,
      completedText: `Weather Data Synchronized`,
      icon: <Sun className="w-4 h-4 text-amber-400" />,
    };
  }

  // Volume
  if (cleanIntent.includes("volume") || cleanIntent.includes("sound")) {
    return {
      name: "System Audio",
      actionVerb: "Adjusting System Volume...",
      completedText: "Volume Adjusted",
      icon: <Volume2 className="w-4 h-4 text-sky-400" />,
    };
  }

  // Brightness
  if (cleanIntent.includes("brightness") || cleanIntent.includes("display")) {
    return {
      name: "Display",
      actionVerb: "Adjusting Display Brightness...",
      completedText: "Brightness Updated",
      icon: <Sun className="w-4 h-4 text-amber-400" />,
    };
  }

  // Timer & Stopwatch & Alarm
  if (cleanIntent.includes("timer") || cleanIntent.includes("alarm") || cleanIntent.includes("stopwatch")) {
    const label = cleanIntent.includes("stopwatch") ? "Stopwatch" : cleanIntent.includes("alarm") ? "Alarm" : "Timer";
    return {
      name: `${label} Engine`,
      actionVerb: `Configuring ${label}...`,
      completedText: `${label} Active`,
      icon: <CalendarIcon className="w-4 h-4 text-amber-400" />,
    };
  }

  // App Launcher
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

  // Generic fallback
  const fallbackName = formatIntentName(cleanIntent || "Agentic Action");
  return {
    name: fallbackName,
    actionVerb: paramVal ? `Executing ${fallbackName} (${paramVal})...` : `Executing ${fallbackName}...`,
    completedText: `${fallbackName} Completed`,
    icon: getDynamicToolIcon(cleanIntent, fallbackName),
  };
}

export interface IntentBridgeHUDProps {
  intent?: string;
  params?: Record<string, any>;
  prompt?: string;
  isDark: boolean;
  colorTheme?: ColorTheme;
  statusText?: string;
  isCompleted?: boolean;
  status?: string;
  historyCount?: number;
  onDismiss?: () => void;
}

export const IntentBridgeHUD: React.FC<IntentBridgeHUDProps> = ({
  intent,
  params,
  prompt,
  isDark,
  colorTheme = "violet",
  statusText,
  isCompleted = false,
  onDismiss,
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const laserGradId = useId();

  const isSuccess =
    isCompleted &&
    (statusText?.toLowerCase().includes("completed") ||
      statusText?.toLowerCase().includes("success") ||
      statusText?.toLowerCase().includes("ready") ||
      statusText?.toLowerCase().includes("retrieved") ||
      statusText?.toLowerCase().includes("done") ||
      statusText?.toLowerCase().includes("finished"));

  const isFailed =
    statusText?.toLowerCase().includes("fail") ||
    statusText?.toLowerCase().includes("error");

  const isOffline =
    isCompleted &&
    (statusText?.toLowerCase().includes("offline") ||
      statusText?.toLowerCase().includes("no internet"));

  const actionInfo = resolveActionInfo(intent, params, prompt);

  const onDismissRef = useRef(onDismiss);
  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  // Auto-dismiss completed HUD bridge smoothly after 7 seconds without resetting timer on parent renders
  useEffect(() => {
    if (isCompleted) {
      const timer = setTimeout(() => {
        onDismissRef.current?.();
      }, 7000);
      return () => clearTimeout(timer);
    }
  }, [isCompleted]);

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
        {/* 2 Connected Interactive Nodes via Laser Stream */}
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
              <span
                className="absolute inset-0 rounded-xl border animate-pulse opacity-60 pointer-events-none"
                style={{ borderColor: theme.accent || "#fff" }}
              />
              <Sparkles className="w-4 h-4 text-white drop-shadow" />
            </div>
            <span className={`text-[10px] font-semibold mt-1 ${isDark ? "text-white" : "text-slate-800"}`}>
              Amigo Core
            </span>
          </div>

          {/* Laser Beam Stream */}
          <div className="relative flex-1 flex items-center justify-center mx-2 h-6 select-none pointer-events-none">
            <svg className="w-full h-4 overflow-visible" fill="none">
              <defs>
                <linearGradient id={laserGradId} x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor={beamGradientStart} stopOpacity="0.3" />
                  <stop offset="50%" stopColor={beamGradientStop} stopOpacity="1" />
                  <stop offset="100%" stopColor={beamGradientStop} stopOpacity="0.4" />
                </linearGradient>
              </defs>
              <line
                x1="2"
                y1="8"
                x2="100%"
                y2="8"
                stroke={isDark ? "rgba(255, 255, 255, 0.12)" : "rgba(0, 0, 0, 0.10)"}
                strokeWidth="1.5"
                strokeLinecap="round"
              />
              <line
                x1="2"
                y1="8"
                x2="100%"
                y2="8"
                stroke={
                  isSuccess
                    ? "#10b981"
                    : isOffline
                    ? isDark
                      ? "#64748b"
                      : "#94a3b8"
                    : isFailed
                    ? "#f59e0b"
                    : `url(#${laserGradId})`
                }
                strokeWidth={isSuccess ? "2" : isOffline ? "1.5" : "2.5"}
                strokeLinecap="round"
                className={isCompleted || isOffline ? "" : "laser-beam-stream"}
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
                  : isOffline
                  ? { borderColor: isDark ? "rgba(255,255,255,0.25)" : "rgba(0,0,0,0.25)" }
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

        {/* Seamless Status Label with Accessible Live Region */}
        <div
          role="status"
          aria-live="polite"
          className="flex items-center justify-center gap-1.5 mt-1 text-[10px] font-medium max-w-[240px] px-2"
        >
          {isSuccess ? (
            <CheckCircle2 className="w-3 h-3 text-emerald-400 flex-shrink-0" />
          ) : isOffline ? (
            <span className="w-1.5 h-1.5 rounded-full bg-slate-400 flex-shrink-0" />
          ) : isFailed ? (
            <AlertCircle className="w-3 h-3 text-amber-400 flex-shrink-0" />
          ) : isCompleted ? (
            <CheckCircle2 className="w-3 h-3 text-emerald-400 flex-shrink-0" />
          ) : (
            <span
              className="w-1.5 h-1.5 rounded-full animate-ping flex-shrink-0"
              style={{ backgroundColor: theme.primary }}
            />
          )}
          <span className={`opacity-80 truncate ${isDark ? "text-slate-300" : "text-slate-700"}`}>
            {statusText ||
              (isSuccess
                ? actionInfo.completedText
                : isOffline
                ? `${actionInfo.name} (Offline)`
                : isCompleted
                ? actionInfo.completedText
                : actionInfo.actionVerb)}
          </span>
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              className="opacity-60 hover:opacity-100 focus:opacity-100 transition-opacity ml-1 p-0.5 rounded-full text-slate-400 hover:text-slate-200 focus:outline-none flex-shrink-0"
              title="Dismiss"
              aria-label="Dismiss action notice"
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
