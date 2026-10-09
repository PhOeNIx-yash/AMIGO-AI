import React, { useRef, useEffect } from "react";
import { motion } from "motion/react";
import { HugeiconsIcon } from "@hugeicons/react";
import {
  AiBrain01Icon,
  AiSparklesIcon,
  SparklesIcon,
  FlashIcon,
  Search01Icon,
  YoutubeIcon,
  SpotifyIcon,
  VolumeHighIcon,
  Sun01Icon,
  Camera01Icon,
  Folder01Icon,
  Mail01Icon,
  Calculator01Icon,
  SlidersHorizontalIcon,
  Rocket01Icon,
  Clock01Icon,
  Timer01Icon,
  CloudIcon,
  CheckmarkCircle02Icon,
  AlertCircleIcon,
  Cancel01Icon,
  ArrowRight01Icon,
} from "@hugeicons/core-free-icons";
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
const POLITE_PREFIXES =
  /^(?:hey\s+amigo|amigo|please|could\s+you(?:\s+please)?|can\s+you(?:\s+please)?|would\s+you(?:\s+mind)?|kindly|i\s+want\s+to|i\s+need\s+to|help\s+me(?:\s+to)?|tell\s+me|what\s+is|what's|how\s+is|how's|check)\s+/i;

// Precompiled action triggers at module level
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

interface ActionVisualInfo {
  name: string;
  category: string;
  actionVerb: string;
  completedText: string;
  icon: any;
  accentColor: string;
  glowColor: string;
}

const SENSITIVE_KEY_PATTERN =
  /(?:key|token|auth|secret|pass|cred|bearer|hash|salt|jwt|session|cookie|private)/i;

function extractDynamicParam(
  params?: Record<string, any>,
  prompt?: string
): string {
  if (params && typeof params === "object") {
    const candidateKeys = [
      "query",
      "q",
      "song",
      "video",
      "app_name",
      "application",
      "app",
      "city",
      "location",
      "target",
      "task",
      "action",
      "command",
      "recipient",
      "to",
      "subject",
      "file",
      "filename",
      "path",
      "title",
      "url",
      "text",
      "message",
    ];
    for (const key of candidateKeys) {
      if (SENSITIVE_KEY_PATTERN.test(key)) continue;
      const val = params[key];
      if (typeof val === "string" && val.trim()) {
        const trimmed = val.trim();
        return trimmed.length > 34 ? trimmed.slice(0, 32) + "..." : trimmed;
      }
    }
    for (const [key, val] of Object.entries(params)) {
      if (SENSITIVE_KEY_PATTERN.test(key) || key.startsWith("_")) continue;
      if (typeof val === "string" && val.trim()) {
        const trimmed = val.trim();
        return trimmed.length > 34 ? trimmed.slice(0, 32) + "..." : trimmed;
      }
    }
  }

  // Fallback: extract target directly from prompt
  if (prompt && prompt.trim()) {
    const clean = prompt.trim().replace(/[.!?]+$/, "");
    const match = clean.match(
      /^(?:open|launch|start|run|play|search\s+for|find)\s+(.+)$/i
    );
    if (match && match[1]) {
      const target = match[1].trim();
      return target.length > 34 ? target.slice(0, 32) + "..." : target;
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

function resolveActionInfo(
  intent?: string,
  params?: Record<string, any>,
  prompt?: string
): ActionVisualInfo {
  const cleanIntent = (intent || "").toLowerCase().trim();
  const p = params || {};
  const pr = (prompt || "").toLowerCase().trim();
  const paramVal = extractDynamicParam(p, prompt);

  // File Search / Explorer
  if (
    cleanIntent.includes("file") ||
    cleanIntent.includes("folder") ||
    (pr.includes("file") && cleanIntent === "find_files")
  ) {
    const q = paramVal || "Documents";
    return {
      name: "File Search",
      category: "Filesystem",
      actionVerb: `Searching files for "${q}"...`,
      completedText: `File Match Located`,
      icon: Folder01Icon,
      accentColor: "#6366f1",
      glowColor: "rgba(99, 102, 241, 0.4)",
    };
  }

  // YouTube
  if (
    cleanIntent.includes("youtube") ||
    (pr.includes("youtube") && /\b(play|watch|stream|listen)\b/.test(pr))
  ) {
    const q = paramVal || "YouTube Video";
    return {
      name: "YouTube",
      category: "Media",
      actionVerb: `Streaming "${q}"...`,
      completedText: `Playing on YouTube`,
      icon: YoutubeIcon,
      accentColor: "#ef4444",
      glowColor: "rgba(239, 68, 68, 0.4)",
    };
  }

  // Spotify / Music
  if (
    cleanIntent.includes("spotify") ||
    (cleanIntent.includes("music") && !cleanIntent.includes("search"))
  ) {
    return {
      name: "Spotify",
      category: "Audio",
      actionVerb: paramVal ? `Playing "${paramVal}"...` : "Routing Audio Stream...",
      completedText: "Playback Updated",
      icon: SpotifyIcon,
      accentColor: "#10b981",
      glowColor: "rgba(16, 185, 129, 0.4)",
    };
  }

  // Web Search / Google
  if (
    cleanIntent === "google_search" ||
    cleanIntent === "web_search" ||
    cleanIntent === "search" ||
    pr.includes("search")
  ) {
    const q = paramVal || prompt || "Web Knowledge";
    return {
      name: "Google Search",
      category: "Web Radar",
      actionVerb: `Searching "${q}"...`,
      completedText: `Results Retrieved`,
      icon: Search01Icon,
      accentColor: "#38bdf8",
      glowColor: "rgba(56, 189, 248, 0.4)",
    };
  }

  // Screen Vision / Screenshot
  if (
    cleanIntent.includes("screen") ||
    cleanIntent.includes("vision") ||
    pr.includes("screenshot")
  ) {
    return {
      name: "Screen Vision",
      category: "Perception",
      actionVerb: "Analyzing Screen Context...",
      completedText: "Screen Analyzed",
      icon: Camera01Icon,
      accentColor: "#06b6d4",
      glowColor: "rgba(6, 182, 212, 0.4)",
    };
  }

  // Weather Radar
  if (
    cleanIntent.includes("weather") ||
    (pr.includes("weather") && !cleanIntent)
  ) {
    const loc = paramVal || "Local Area";
    return {
      name: "Weather Radar",
      category: "Atmosphere",
      actionVerb: `Checking Weather for ${loc}...`,
      completedText: `Weather Synchronized`,
      icon: CloudIcon,
      accentColor: "#f59e0b",
      glowColor: "rgba(245, 158, 11, 0.4)",
    };
  }

  // System Volume
  if (cleanIntent.includes("volume") || cleanIntent.includes("sound")) {
    return {
      name: "System Audio",
      category: "Hardware",
      actionVerb: "Adjusting System Volume...",
      completedText: "Volume Adjusted",
      icon: VolumeHighIcon,
      accentColor: "#0ea5e9",
      glowColor: "rgba(14, 165, 233, 0.4)",
    };
  }

  // Display Brightness
  if (cleanIntent.includes("brightness") || cleanIntent.includes("display")) {
    return {
      name: "Display",
      category: "Hardware",
      actionVerb: "Adjusting Display Brightness...",
      completedText: "Brightness Updated",
      icon: Sun01Icon,
      accentColor: "#fbbf24",
      glowColor: "rgba(251, 191, 36, 0.4)",
    };
  }

  // Timer & Stopwatch
  if (
    cleanIntent.includes("timer") ||
    cleanIntent.includes("alarm") ||
    cleanIntent.includes("stopwatch")
  ) {
    const isStopwatch = cleanIntent.includes("stopwatch");
    const isAlarm = cleanIntent.includes("alarm");
    const label = isStopwatch ? "Stopwatch" : isAlarm ? "Alarm" : "Timer";
    return {
      name: `${label} Engine`,
      category: "Chrono",
      actionVerb: `Configuring ${label}...`,
      completedText: `${label} Active`,
      icon: isStopwatch ? Clock01Icon : Timer01Icon,
      accentColor: "#14b8a6",
      glowColor: "rgba(20, 184, 166, 0.4)",
    };
  }

  // Application Launcher
  if (
    cleanIntent.includes("open") ||
    cleanIntent.includes("launch") ||
    pr.startsWith("open ") ||
    pr.startsWith("launch ")
  ) {
    let appName = paramVal || (p.app_name || p.application);
    if (!appName || appName === "Application") {
      const match = pr.match(/^(?:open|launch|start|run)\s+(.+)$/i);
      appName = match ? formatIntentName(match[1]) : "Application";
    } else {
      appName = formatIntentName(appName);
    }

    return {
      name: appName,
      category: "Application",
      actionVerb: `Launching ${appName}...`,
      completedText: `${appName} Active`,
      icon: Rocket01Icon,
      accentColor: "#8b5cf6",
      glowColor: "rgba(139, 92, 246, 0.4)",
    };
  }

  // Settings & System Controls
  if (cleanIntent.includes("setting") || cleanIntent.includes("config")) {
    return {
      name: "System Settings",
      category: "Preferences",
      actionVerb: "Updating System Settings...",
      completedText: "Settings Applied",
      icon: SlidersHorizontalIcon,
      accentColor: "#a855f7",
      glowColor: "rgba(168, 85, 247, 0.4)",
    };
  }

  // Mail & Communication
  if (cleanIntent.includes("mail") || cleanIntent.includes("message")) {
    return {
      name: "Communication",
      category: "Messaging",
      actionVerb: "Drafting Communication...",
      completedText: "Message Prepared",
      icon: Mail01Icon,
      accentColor: "#3b82f6",
      glowColor: "rgba(59, 130, 246, 0.4)",
    };
  }

  // Calculator
  if (cleanIntent.includes("calc") || cleanIntent.includes("math")) {
    return {
      name: "Math Engine",
      category: "Computation",
      actionVerb: "Computing Expression...",
      completedText: "Calculation Finished",
      icon: Calculator01Icon,
      accentColor: "#10b981",
      glowColor: "rgba(16, 185, 129, 0.4)",
    };
  }

  // Generic fallback
  const fallbackName = formatIntentName(cleanIntent || "Agentic Action");
  return {
    name: fallbackName,
    category: "Amigo Automation",
    actionVerb: paramVal ? `Executing ${fallbackName} (${paramVal})...` : `Executing ${fallbackName}...`,
    completedText: `${fallbackName} Completed`,
    icon: FlashIcon,
    accentColor: "#ec4899",
    glowColor: "rgba(236, 72, 153, 0.4)",
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

/**
 * IntentBridgeHUD (Customized for Amigo)
 * Ultra-sleek, dynamic agentic action capsule tailored specifically for Amigo.
 * Built with Motion and Hugeicons for smooth, responsive micro-interactions.
 */
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
  const paramSnippet = extractDynamicParam(params, prompt);

  const onDismissRef = useRef(onDismiss);
  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  // Smooth auto-dismiss after completion
  useEffect(() => {
    if (isCompleted) {
      const timer = setTimeout(() => {
        onDismissRef.current?.();
      }, 7000);
      return () => clearTimeout(timer);
    }
  }, [isCompleted]);

  // Primary active display state
  const stateLabel = isSuccess
    ? actionInfo.completedText
    : isOffline
    ? `${actionInfo.name} (Offline)`
    : isFailed
    ? "Execution Interrupted"
    : isCompleted
    ? actionInfo.completedText
    : actionInfo.actionVerb;

  return (
    <motion.div
      initial={{ opacity: 0, y: 14, scale: 0.94, filter: "blur(6px)" }}
      animate={{ opacity: 1, y: 0, scale: 1, filter: "blur(0px)" }}
      exit={{ opacity: 0, y: 8, scale: 0.95, filter: "blur(4px)" }}
      transition={{ type: "spring", stiffness: 380, damping: 28 }}
      whileHover={{ scale: 1.015 }}
      className="relative pointer-events-auto select-none"
    >
      {/* Ambient Radial Backlight Glow */}
      <div
        className="absolute -inset-1.5 rounded-full opacity-35 blur-xl pointer-events-none transition-all duration-500"
        style={{
          background: isSuccess
            ? "radial-gradient(ellipse at center, rgba(16, 185, 129, 0.45), transparent 70%)"
            : isFailed
            ? "radial-gradient(ellipse at center, rgba(245, 158, 11, 0.45), transparent 70%)"
            : `radial-gradient(ellipse at center, ${theme.glow}, ${actionInfo.glowColor}, transparent 70%)`,
        }}
      />

      {/* Main Glass HUD Capsule */}
      <div
        className={`relative flex items-center gap-2.5 sm:gap-3 px-3.5 py-2 sm:px-4 sm:py-2.5 rounded-full border backdrop-blur-2xl shadow-2xl transition-colors duration-300 ${
          isDark
            ? "bg-slate-950/85 border-white/12 text-slate-100 shadow-black/60"
            : "bg-white/85 border-slate-200/90 text-slate-800 shadow-slate-300/40"
        }`}
      >
        {/* Top-rim highlight shimmer */}
        <div className="absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-white/25 to-transparent pointer-events-none" />

        {/* 1. Amigo Neural Core Indicator */}
        <div className="relative flex items-center justify-center flex-shrink-0">
          <motion.div
            animate={
              !isCompleted
                ? {
                    scale: [1, 1.15, 1],
                    opacity: [0.6, 0.9, 0.6],
                  }
                : { scale: 1, opacity: 0.6 }
            }
            transition={{
              repeat: isCompleted ? 0 : Infinity,
              duration: 2.2,
              ease: "easeInOut",
            }}
            className="absolute -inset-1 rounded-full blur-sm"
            style={{
              background: isSuccess ? "#10b981" : theme.primary,
            }}
          />
          <div
            className="relative w-8 h-8 rounded-full flex items-center justify-center shadow-md ring-1 ring-white/20"
            style={{
              background: theme.gradient,
            }}
          >
            <HugeiconsIcon
              icon={isCompleted ? SparklesIcon : AiSparklesIcon}
              size={15}
              strokeWidth={1.8}
              className="text-white drop-shadow"
            />
          </div>
        </div>

        {/* 2. Fluid Neural Energy Conduit (GPU Accelerated SVG Laser) */}
        <div className="relative flex items-center justify-center w-10 sm:w-14 h-5 select-none pointer-events-none flex-shrink-0">
          <svg width="100%" height="4" className="absolute overflow-visible" style={{ filter: "drop-shadow(0 0 3px rgba(255,255,255,0.4))" }}>
            {/* Background Track */}
            <line 
              x1="0" y1="2" x2="100%" y2="2" 
              stroke={isDark ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.1)"} 
              strokeWidth="2" 
              strokeLinecap="round" 
            />
            
            {/* Animated Laser Beam */}
            <line 
              x1="0" y1="2" x2="100%" y2="2" 
              stroke={isSuccess ? "#10b981" : isFailed ? "#f59e0b" : theme.primary}
              strokeWidth="2.5" 
              strokeLinecap="round"
              className={!isCompleted ? "laser-beam-stream" : ""}
              style={{
                transition: "stroke 0.4s ease",
                opacity: isCompleted ? 0.3 : 1
              }}
            />
          </svg>

          {/* Micro dispatch chevron */}
          <motion.div
            animate={
              !isCompleted
                ? { x: [-3, 3, -3], opacity: [0.5, 1, 0.5] }
                : { x: 0, opacity: 0.4 }
            }
            transition={{
              repeat: isCompleted ? 0 : Infinity,
              duration: 1.2,
              ease: "easeInOut",
            }}
            className="absolute flex items-center justify-center z-10"
          >
            <div className={`p-0.5 rounded-full shadow-sm ${isDark ? "bg-slate-900" : "bg-white"}`}>
              <HugeiconsIcon
                icon={ArrowRight01Icon}
                size={11}
                strokeWidth={2.5}
                className={isDark ? "text-white" : "text-slate-800"}
              />
            </div>
          </motion.div>
        </div>

        {/* 3. Target Module Badge */}
        <div className="relative flex items-center justify-center flex-shrink-0">
          <div
            className={`relative w-8 h-8 rounded-full flex items-center justify-center border transition-all duration-300 ${
              isDark
                ? "bg-slate-900/90 border-white/10"
                : "bg-slate-100 border-slate-200"
            }`}
            style={{
              borderColor: isSuccess
                ? "#10b981"
                : isFailed
                ? "#f59e0b"
                : actionInfo.accentColor + "55",
              boxShadow: isSuccess
                ? "0 0 12px rgba(16, 185, 129, 0.35)"
                : isFailed
                ? "0 0 12px rgba(245, 158, 11, 0.35)"
                : `0 0 12px ${actionInfo.glowColor}`,
            }}
          >
            <HugeiconsIcon
              icon={actionInfo.icon}
              size={16}
              strokeWidth={1.75}
              style={{
                color: isSuccess
                  ? "#10b981"
                  : isFailed
                  ? "#f59e0b"
                  : actionInfo.accentColor,
              }}
            />
          </div>
        </div>

        {/* 4. Action Context & Telemetry Details */}
        <div className="flex flex-col min-w-0 pr-1 max-w-[140px] sm:max-w-[220px]">
          {/* Top row: Target Name + Parameter chip */}
          <div className="flex items-center gap-1.5 truncate">
            <span
              className={`text-[11px] font-semibold tracking-tight truncate ${
                isDark ? "text-white" : "text-slate-900"
              }`}
            >
              {actionInfo.name}
            </span>

            {paramSnippet && (
              <span
                className={`text-[9.5px] font-medium px-1.5 py-0.2 rounded-full border truncate hidden sm:inline-block ${
                  isDark
                    ? "bg-white/5 border-white/10 text-slate-300"
                    : "bg-slate-100 border-slate-200 text-slate-600"
                }`}
              >
                {paramSnippet}
              </span>
            )}
          </div>

          {/* Bottom row: Live Status Telemetry */}
          <div className="flex items-center gap-1.5 mt-0.5">
            {isSuccess ? (
              <HugeiconsIcon
                icon={CheckmarkCircle02Icon}
                size={11}
                strokeWidth={2.2}
                className="text-emerald-400 flex-shrink-0"
              />
            ) : isFailed ? (
              <HugeiconsIcon
                icon={AlertCircleIcon}
                size={11}
                strokeWidth={2.2}
                className="text-amber-400 flex-shrink-0"
              />
            ) : isOffline ? (
              <span className="w-1.5 h-1.5 rounded-full bg-slate-400 flex-shrink-0" />
            ) : (
              <span
                className="w-1.5 h-1.5 rounded-full animate-ping flex-shrink-0"
                style={{ backgroundColor: theme.primary }}
              />
            )}

            <span
              className={`text-[10px] font-medium truncate ${
                isDark ? "text-slate-300/85" : "text-slate-600"
              }`}
            >
              {statusText || stateLabel}
            </span>
          </div>
        </div>

        {/* 5. Quick Dismiss Control */}
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss action notification"
            className={`p-1 rounded-full transition-all flex-shrink-0 ${
              isDark
                ? "text-slate-400 hover:text-white hover:bg-white/10"
                : "text-slate-500 hover:text-slate-800 hover:bg-slate-200"
            }`}
          >
            <HugeiconsIcon icon={Cancel01Icon} size={13} strokeWidth={2} />
          </button>
        )}
      </div>
    </motion.div>
  );
};

export default IntentBridgeHUD;
