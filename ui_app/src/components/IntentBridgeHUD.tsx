import React from "react";
import { motion, AnimatePresence } from "motion/react";
import { CheckCircle2, Circle, Loader2, AlertCircle, WifiOff } from "lucide-react";
import { ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";

const NON_ACTION_INTENTS = new Set([
  "chat",
  "ask_document",
  "search_knowledge",
  "summarize_document",
  "custom_response",
  "error",
]);

// Generalized Action Intent Detection
export function isActionIntent(text: string, intent?: string): boolean {
  // If backend intent is explicitly known, honor it directly
  if (intent !== undefined && intent !== null && intent !== "") {
    return !NON_ACTION_INTENTS.has(intent.toLowerCase());
  }

  // During initial prompt state (before server response), check for genuine system action triggers
  if (!text || !text.trim()) return false;
  const clean = text.toLowerCase().replace(/[_]/g, " ").trim();

  // If the prompt is a natural question, it is not a system action HUD trigger
  if (/^(?:who|what|when|where|why|how|can\s+you\s+tell|tell\s+me|is\s+there|explain|describe)\b/i.test(clean)) {
    return false;
  }

  const actionPrefixes = [
    /^(?:open|launch|start|run|close|kill|terminate)\b/i,
    /^(?:play|pause|resume|skip|next|prev|previous)\b/i,
    /^(?:set\s+(?:volume|brightness|timer|reminder)|volume\s+|brightness\s+|mute|unmute)\b/i,
    /^(?:take\s+screenshot|capture\s+screen|snip)\b/i,
    /^(?:lock\s+pc|sleep\s+pc|restart\s+pc|empty\s+recycle\s+bin)\b/i,
    /^(?:weather\s+in|check\s+weather|forecast)\b/i,
    /^(?:system\s+status|cpu\s+usage|battery\s+status|hardware\s+metrics)\b/i,
    /^(?:stopwatch|start\s+stopwatch|set\s+timer)\b/i,
  ];

  return actionPrefixes.some((p) => p.test(clean));
}


// Real High-Resolution Vector Brand & System Logos
const YouTubeIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
    <path
      d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814z"
      fill="#FF0000"
    />
    <path d="M9.545 15.568V8.432L15.818 12l-6.273 3.568z" fill="#FFFFFF" />
  </svg>
);

const GoogleIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24">
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
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24">
    <circle cx="12" cy="12" r="12" fill="#1DB954" />
    <path
      d="M17.5 16.3c-.2.3-.5.4-.8.2-2.3-1.4-5.2-1.7-8.6-.9-.3.1-.7-.1-.8-.4-.1-.3.1-.7.4-.8 3.8-.9 7-.5 9.6 1.1.3.2.4.5.2.8zm1.2-2.6c-.2.4-.7.5-1.1.3-2.6-1.6-6.6-2.1-9.7-1.1-.4.1-.9-.1-1-.5-.1-.4.1-.9.5-1 3.6-1.1 8-.5 11 1.3.4.2.5.7.3 1zm.1-2.7c-3.1-1.9-8.3-2-11.3-1.1-.5.1-1-.2-1.1-.7-.1-.5.2-1 .7-1.1 3.5-1.1 9.2-.9 12.8 1.2.4.3.6.9.3 1.3-.3.5-.9.6-1.4.4z"
      fill="#FFFFFF"
    />
  </svg>
);

const DiscordIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="#5865F2">
    <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994.021-.041.001-.09-.041-.106a13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128c.126-.093.252-.19.372-.287a.075.075 0 0 1 .077-.01c3.931 1.795 8.18 1.795 12.061 0a.075.075 0 0 1 .078.009c.12.098.246.195.373.288a.077.077 0 0 1-.006.127c-.598.35-1.22.645-1.873.891a.076.076 0 0 0-.04.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.028zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" />
  </svg>
);

const WindowsIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24">
    <path d="M0 0h11.377v11.372H0V0zm12.623 0H24v11.372H12.623V0zM0 12.623h11.377V24H0V12.623zm12.623 0H24V24H12.623V12.623z" fill="#0078D4" />
  </svg>
);

const ChromeIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24">
    <circle cx="12" cy="12" r="11" fill="#4285F4" />
    <path d="M12 2a10 10 0 0 1 8.66 5H12v5h9.8A10 10 0 0 1 12 22a10 10 0 0 1-8.66-15h8.66V2z" fill="#EA4335" />
    <circle cx="12" cy="12" r="5" fill="#FBBC05" />
    <circle cx="12" cy="12" r="3" fill="#34A853" />
    <circle cx="12" cy="12" r="2" fill="#FFFFFF" />
  </svg>
);

const NotepadIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
    <rect x="3" y="2" width="18" height="20" rx="3" fill="#0078D4" />
    <path d="M7 7h10M7 11h10M7 15h6" stroke="#FFFFFF" strokeWidth="1.8" strokeLinecap="round" />
  </svg>
);

const CalculatorIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
    <rect x="3" y="2" width="18" height="20" rx="3" fill="#107C41" />
    <rect x="6" y="5" width="12" height="4" rx="1" fill="#FFFFFF" fillOpacity="0.3" />
    <circle cx="7.5" cy="12.5" r="1.2" fill="#FFFFFF" />
    <circle cx="12" cy="12.5" r="1.2" fill="#FFFFFF" />
    <circle cx="16.5" cy="12.5" r="1.2" fill="#FFFFFF" />
    <circle cx="7.5" cy="16.5" r="1.2" fill="#FFFFFF" />
    <circle cx="12" cy="16.5" r="1.2" fill="#FFFFFF" />
    <rect x="15.3" y="15.3" width="2.4" height="2.4" rx="0.5" fill="#FFFFFF" />
  </svg>
);

const CalendarIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
    <rect x="3" y="4" width="18" height="18" rx="3" fill="#F7630C" />
    <path d="M3 9h18" stroke="#FFFFFF" strokeWidth="1.8" />
    <rect x="7" y="2" width="2" height="4" rx="1" fill="#FFFFFF" />
    <rect x="15" y="2" width="2" height="4" rx="1" fill="#FFFFFF" />
    <circle cx="8" cy="14" r="1" fill="#FFFFFF" />
    <circle cx="12" cy="14" r="1" fill="#FFFFFF" />
    <circle cx="16" cy="14" r="1" fill="#FFFFFF" />
  </svg>
);

const VolumeIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
    <rect x="2" y="2" width="20" height="20" rx="4" fill="#8E44AD" />
    <path d="M7 9v6h3l4 4V5l-4 4H7z" fill="#FFFFFF" />
    <path d="M17 9c.7 1 .7 3 0 4" stroke="#FFFFFF" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
);

const CameraIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
    <rect x="2" y="2" width="20" height="20" rx="4" fill="#00B4D8" />
    <path d="M6 8h2l1-2h6l1 2h2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z" fill="#FFFFFF" />
    <circle cx="12" cy="13" r="3" fill="#0077B6" />
  </svg>
);

const WeatherIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
    <rect x="2" y="2" width="20" height="20" rx="4" fill="#0284C7" />
    <circle cx="9" cy="9" r="3" fill="#FBBF24" />
    <path d="M7 16a3 3 0 0 1 5.83-1 2.5 2.5 0 0 1 4.17 2H7z" fill="#FFFFFF" />
    <path d="M10 18v2M14 18v2" stroke="#38BDF8" strokeWidth="1.4" strokeLinecap="round" />
  </svg>
);

const FolderIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
    <path d="M2 6a2 2 0 0 1 2-2h4.586a1 1 0 0 1 .707.293L11.707 6.7a1 1 0 0 0 .707.293H20a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6z" fill="#FBBF24" />
    <path d="M2 9h20v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9z" fill="#F59E0B" />
  </svg>
);

const WikipediaIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
    <rect x="2" y="2" width="20" height="20" rx="4" fill="#202124" />
    <path d="M6 7l3.5 10L12 9l2.5 8L18 7" stroke="#FFFFFF" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const CpuIcon = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
    <rect x="4" y="4" width="16" height="16" rx="3" fill="#6366F1" />
    <rect x="8" y="8" width="8" height="8" rx="1" fill="#FFFFFF" fillOpacity="0.3" />
    <path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" stroke="#FFFFFF" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

const AmigoSparkleLogo = () => (
  <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none">
    <path
      d="M12 0C12 6.627 17.373 12 24 12C17.373 12 12 17.373 12 24C12 17.373 6.627 12 0 12C6.627 12 12 6.627 12 0Z"
      fill="#FFFFFF"
    />
  </svg>
);

const AppIcon: React.FC<{ name: string }> = ({ name }) => {
  const n = (name || "").toLowerCase();
  if (n.includes("chrome") || n.includes("browser") || n.includes("web")) return <ChromeIcon />;
  if (n.includes("spotify") || n.includes("music") || n.includes("song")) return <SpotifyIcon />;
  if (n.includes("youtube") || n.includes("video")) return <YouTubeIcon />;
  if (n.includes("google") || n.includes("search")) return <GoogleIcon />;
  if (n.includes("discord")) return <DiscordIcon />;
  if (n.includes("notepad") || n.includes("text") || n.includes("note") || n.includes("editor")) return <NotepadIcon />;
  if (n.includes("calc")) return <CalculatorIcon />;
  if (n.includes("calendar") || n.includes("schedule")) return <CalendarIcon />;
  if (n.includes("wiki")) return <WikipediaIcon />;
  if (n.includes("folder") || n.includes("explorer") || n.includes("file")) return <FolderIcon />;
  if (n.includes("camera") || n.includes("photo")) return <CameraIcon />;
  if (n.includes("weather")) return <WeatherIcon />;
  if (n.includes("cpu") || n.includes("task manager") || n.includes("performance") || n.includes("monitor")) return <CpuIcon />;
  return <WindowsIcon />;
};

interface AppInfo {
  name: string;
  actionVerb: string;
  icon: React.ReactNode;
}

// Clean, Data-Driven Entity & Intent Resolver
function parseActionEntity(prompt: string, intent?: string): AppInfo {
  const cleanPrompt = (prompt || "").trim();
  const cleanIntent = (intent || "").trim().toLowerCase();

  // Intent-based Direct Mapping
  switch (cleanIntent) {
    case "stopwatch":
      return {
        name: "Stopwatch",
        actionVerb: "Initializing Live Stopwatch...",
        icon: (
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="9" stroke="#06B6D4" strokeWidth="2" />
            <path d="M12 7v5l3 3" stroke="#FFFFFF" strokeWidth="2" strokeLinecap="round" />
          </svg>
        ),
      };

    case "set_timer":
    case "timer":
      return {
        name: "Countdown Timer",
        actionVerb: "Initializing Live Timer...",
        icon: (
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="9" stroke="#F59E0B" strokeWidth="2" />
            <path d="M12 7v5l3 3" stroke="#FFFFFF" strokeWidth="2" strokeLinecap="round" />
          </svg>
        ),
      };

    case "get_weather":
      return {
        name: "Weather Radar",
        actionVerb: "Fetching Meteorological Data...",
        icon: <WeatherIcon />,
      };

    case "play_youtube":
    case "search_youtube":
      return {
        name: "YouTube",
        actionVerb: "Connecting to YouTube...",
        icon: <YouTubeIcon />,
      };

    case "calculate":
      return {
        name: "Calculator",
        actionVerb: "Calculating with Math Engine...",
        icon: <CalculatorIcon />,
      };

    case "set_reminder":
    case "list_reminders":
    case "cancel_reminder":
      return {
        name: "Scheduler",
        actionVerb: "Scheduling Event Hook...",
        icon: <CalendarIcon />,
      };

    case "get_time":
    case "get_date":
      return {
        name: "World Clock",
        actionVerb: "Querying System Time...",
        icon: <CalendarIcon />,
      };

    case "find_file":
    case "open_file":
    case "open_folder":
    case "reveal_file":
      return {
        name: "File Explorer",
        actionVerb: "Indexing File System...",
        icon: <FolderIcon />,
      };

    case "volume_up":
    case "volume_down":
    case "set_volume":
    case "mute":
    case "unmute":
      return {
        name: "System Audio",
        actionVerb: "Adjusting System Audio...",
        icon: <VolumeIcon />,
      };

    case "take_screenshot":
    case "read_screen":
    case "ask_about_screen":
      return {
        name: "Screen Vision",
        actionVerb: "Capturing Screen Vision...",
        icon: <CameraIcon />,
      };

    case "system_status":
    case "hardware_metrics":
      return {
        name: "Hardware Telemetry",
        actionVerb: "Querying Hardware Stats...",
        icon: <CpuIcon />,
      };

    case "lock_pc":
      return {
        name: "Windows Security",
        actionVerb: "Locking Workstation...",
        icon: <WindowsIcon />,
      };

    case "sleep_pc":
      return {
        name: "Windows Power",
        actionVerb: "Entering Sleep Mode...",
        icon: <WindowsIcon />,
      };

    case "restart_pc":
      return {
        name: "Windows Power",
        actionVerb: "Initiating Restart...",
        icon: <WindowsIcon />,
      };

    case "empty_recycle_bin":
      return {
        name: "Recycle Bin",
        actionVerb: "Purging Recycle Bin...",
        icon: <WindowsIcon />,
      };

    default:
      break;
  }

  // Dynamic App Name Resolution for general app launch / close actions
  const launchMatch = cleanPrompt.match(/\b(?:open|launch|start|run|close|switch to)\s+([a-zA-Z0-9_\-\.\s]+)/i);
  if (launchMatch && launchMatch[1]) {
    const rawTarget = launchMatch[1].replace(/\b(please|now|for me|app|application)\b/gi, "").trim();
    if (rawTarget.length > 0 && rawTarget.length < 30) {
      const formattedName = rawTarget
        .split(" ")
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join(" ");
      return {
        name: formattedName,
        actionVerb: `Launching ${formattedName}...`,
        icon: <AppIcon name={formattedName} />,
      };
    }
  }

  // Universal Fallback
  return {
    name: "System Action",
    actionVerb: "Executing Requested Action...",
    icon: <AmigoSparkleLogo />,
  };
}

interface IntentBridgeHUDProps {
  prompt: string;
  isDark: boolean;
  colorTheme?: ColorTheme;
  statusText?: string;
  isCompleted?: boolean;
  status?: string;
  intent?: string;
  historyCount?: number;
  onDismiss?: () => void;
}

export const IntentBridgeHUD: React.FC<IntentBridgeHUDProps> = ({
  prompt,
  isDark,
  colorTheme = "violet",
  statusText,
  isCompleted = false,
  status,
  intent,
  historyCount = 0,
  onDismiss,
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const app = parseActionEntity(prompt, intent);

  const isOffline = status === "offline" || statusText?.toLowerCase().includes("offline");
  const isFailed = status === "failed" || statusText?.toLowerCase().includes("failed");
  const isSuccess = isCompleted && !isOffline && !isFailed;

  // Auto-dismiss completed HUD pill after 15 seconds or user click
  React.useEffect(() => {
    if (isCompleted && onDismiss) {
      const timer = setTimeout(() => {
        onDismiss();
      }, 15000);
      return () => clearTimeout(timer);
    }
  }, [isCompleted, onDismiss]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 10, scale: 0.92 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8, scale: 0.94 }}
      transition={{ type: "spring", stiffness: 380, damping: 26 }}
      onClick={onDismiss}
      className={`flex w-full max-w-full items-center justify-center mx-auto my-3.5 ${onDismiss ? "cursor-pointer" : ""}`}
      title={onDismiss ? "Click to dismiss" : undefined}
    >
      {/* Gemini-Style App Extension Connector Pill with Fluid Glow & Morphing Aura */}
      <motion.div
        animate={{
          borderColor: isOffline
            ? "rgba(244, 63, 94, 0.45)"
            : isFailed
            ? "rgba(239, 68, 68, 0.45)"
            : isSuccess
            ? "rgba(16, 185, 129, 0.55)"
            : `${theme.primary}55`,
          boxShadow: isOffline
            ? "0 4px 22px rgba(244, 63, 94, 0.25)"
            : isFailed
            ? "0 4px 22px rgba(239, 68, 68, 0.25)"
            : isSuccess
            ? "0 4px 25px rgba(16, 185, 129, 0.32), 0 0 12px rgba(16, 185, 129, 0.18)"
            : `0 4px 20px ${theme.glow}35, 0 1px 4px rgba(0,0,0,0.12)`,
        }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className={`relative flex min-w-0 max-w-[calc(100vw-2rem)] items-center justify-center gap-x-2.5 px-4 py-2 rounded-full border backdrop-blur-2xl transition-colors select-none ${
          isDark
            ? "bg-slate-950/85 text-slate-100 shadow-indigo-950/50"
            : "bg-white/90 text-slate-900 shadow-indigo-200/50"
        }`}
      >
        {/* 1. Amigo Logo Node with Organic Pulse */}
        <motion.div
          animate={{ scale: isCompleted ? [1, 1.05, 1] : [1, 1.1, 1] }}
          transition={{ repeat: Infinity, duration: 2.0, ease: "easeInOut" }}
          className="w-5 h-5 rounded-full flex items-center justify-center text-white shadow-sm flex-shrink-0"
          style={{ background: isCompleted ? "linear-gradient(135deg, #10B981, #059669)" : theme.gradient }}
        >
          <AmigoSparkleLogo />
        </motion.div>

        {/* 2. Fluid Energy Conduit Beam */}
        <div className="flex items-center space-x-1 flex-shrink-0">
          <motion.div
            animate={{
              width: isCompleted ? 26 : 22,
              backgroundColor: isCompleted ? "#10B981" : `${theme.primary}35`,
            }}
            transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
            className="h-[2px] rounded-full overflow-hidden relative"
          >
            <AnimatePresence>
              {!isCompleted && (
                <motion.div
                  key="energy-pulse"
                  initial={{ x: "-100%" }}
                  animate={{ x: "200%" }}
                  exit={{ opacity: 0 }}
                  transition={{ repeat: Infinity, duration: 0.85, ease: "linear" }}
                  className="w-1/2 h-full rounded-full"
                  style={{
                    background: `linear-gradient(90deg, transparent, ${theme.accent || "#fff"}, transparent)`,
                  }}
                />
              )}
            </AnimatePresence>
          </motion.div>
        </div>

        {/* 3. Real Vector Brand App Logo Node with Smooth Morphing to Emerald Checkmark / Rose Offline */}
        <div className="relative flex items-center justify-center flex-shrink-0 w-4 h-4">
          <AnimatePresence mode="wait">
            {isOffline ? (
              <motion.div
                key="offline-icon"
                initial={{ scale: 0.3, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.5, opacity: 0 }}
                className="w-4 h-4 rounded-full bg-rose-500 flex items-center justify-center shadow-md shadow-rose-500/30"
              >
                <WifiOff className="w-2.5 h-2.5 text-white" />
              </motion.div>
            ) : isFailed ? (
              <motion.div
                key="failed-icon"
                initial={{ scale: 0.3, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.5, opacity: 0 }}
                className="w-4 h-4 rounded-full bg-amber-500 flex items-center justify-center shadow-md shadow-amber-500/30"
              >
                <AlertCircle className="w-2.5 h-2.5 text-white" />
              </motion.div>
            ) : isSuccess ? (
              <motion.div
                key="completed-check"
                initial={{ scale: 0.3, rotate: -30, opacity: 0 }}
                animate={{ scale: 1, rotate: 0, opacity: 1 }}
                exit={{ scale: 0.5, opacity: 0 }}
                transition={{ type: "spring", stiffness: 480, damping: 22 }}
                className="w-4 h-4 rounded-full bg-emerald-500 flex items-center justify-center shadow-md shadow-emerald-500/30"
              >
                <CheckCircle2 className="w-3 h-3 text-white" />
              </motion.div>
            ) : (
              <motion.div
                key="app-icon"
                initial={{ scale: 0.8, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.6, opacity: 0 }}
                transition={{ duration: 0.25 }}
                className="flex items-center justify-center"
              >
                {app.icon}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* 4. Action Status Text with Crossfade Slide Easing */}
        <div className="flex min-w-0 max-w-[min(48vw,22rem)] items-center space-x-1.5 pl-1 pr-0.5 text-[11.5px] font-medium tracking-tight overflow-hidden">
          <AnimatePresence mode="wait">
            <motion.span
              key={statusText || (isOffline ? "offline" : isFailed ? "failed" : isSuccess ? "status-completed" : app.actionVerb)}
              initial={{ opacity: 0, y: 3, filter: "blur(2px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -3, filter: "blur(2px)" }}
              transition={{ duration: 0.25, ease: "easeOut" }}
              className={`min-w-0 truncate transition-colors ${
                isOffline
                  ? "text-rose-400 font-semibold"
                  : isFailed
                  ? "text-amber-400 font-semibold"
                  : isSuccess
                  ? "text-emerald-400 font-semibold"
                  : "opacity-90"
              }`}
            >
              {statusText || (isOffline ? "Offline" : isFailed ? "Action Failed" : isSuccess ? `${app.name} Ready` : app.actionVerb)}
            </motion.span>
          </AnimatePresence>
        </div>

        {/* 5. Stage Badge Indicator */}
        <div className="flex items-center space-x-1.5 pl-1 flex-shrink-0">
          <span
            className={`text-[10px] px-2 py-0.5 rounded-full font-mono border flex items-center space-x-1 ${
              isOffline
                ? "bg-rose-500/15 text-rose-400 border-rose-500/30 font-semibold"
                : isFailed
                ? "bg-amber-500/15 text-amber-400 border-amber-500/30 font-semibold"
                : isSuccess
                ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30 font-semibold"
                : "bg-indigo-500/15 text-indigo-300 border-indigo-500/30"
            }`}
          >
            {isOffline ? (
              <>
                <WifiOff className="w-2.5 h-2.5 text-rose-400" />
                <span>Offline</span>
              </>
            ) : isFailed ? (
              <>
                <AlertCircle className="w-2.5 h-2.5 text-amber-400" />
                <span>Failed</span>
              </>
            ) : isSuccess ? (
              <>
                <CheckCircle2 className="w-2.5 h-2.5 text-emerald-400" />
                <span>Completed</span>
              </>
            ) : statusText === "Executing Action..." ? (
              <>
                <Loader2 className="w-2.5 h-2.5 animate-spin text-indigo-400" />
                <span>Executing</span>
              </>
            ) : (
              <>
                <Loader2 className="w-2.5 h-2.5 animate-spin text-indigo-400" />
                <span>Processing</span>
              </>
            )}
          </span>
        </div>
      </motion.div>
    </motion.div>
  );
};
