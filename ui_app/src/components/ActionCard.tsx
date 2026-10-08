import React, { useState, useEffect, useRef } from "react";
import { motion } from "motion/react";
import { scaleFade, staggerContainer, staggerItem } from "../utils/motionConfig";
import {
  MessageSquare,
  Utensils,
  MapPin,
  Calendar,
  Check,
  Zap,
  Code2,
  ExternalLink,
  CloudSun,
  Sun,
  CloudRain,
  CloudLightning,
  Snowflake,
  Cloud,
  Droplets,
  Wind,
  Compass,
  Thermometer,
  RotateCw,
  Search,
  Play,
  Pause,
  RotateCcw,
  Timer,
  AlarmClock,
  Clock,
  FolderOpen,
  FileText,
  Copy,
  Music,
  SkipForward,
  SkipBack,
  Volume2,
  VolumeX,
  Sparkles,
  Globe,
  Settings,
  Cpu,
  X,
  AlertCircle,
} from "lucide-react";
import { ActionCardItem, ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { sfx } from "../utils/audio";

interface ActionCardProps {
  items: ActionCardItem[];
  onToggleItem: (id: string) => void;
  onExecuteSingleItem?: (item: ActionCardItem) => void;
  onConfirm: () => void;
  onCancel: () => void;
  onRetry?: () => void;
  isDark: boolean;
  colorTheme?: ColorTheme;
}

const isSafeHttpUrl = (url?: string): boolean => {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
};

// Unified item type detectors to eliminate inconsistent matching
export const isWeatherItem = (item: ActionCardItem): boolean => {
  const type = (item.type || "").toLowerCase();
  const tool = (item.payload?.tool || "").toLowerCase();
  const badge = (item.badge || "").toLowerCase();
  return (
    type === "weather" ||
    tool === "get_weather" ||
    badge === "weather" ||
    /^weather\b/i.test(item.title)
  );
};

export const isTimerItem = (item: ActionCardItem): boolean => {
  const type = (item.type || "").toLowerCase();
  const tool = (item.payload?.tool || "").toLowerCase();
  const mode = (item.payload?.mode || "").toLowerCase();
  const badge = (item.badge || "").toLowerCase();
  return (
    type === "timer" ||
    type === "stopwatch" ||
    tool === "set_timer" ||
    tool === "stopwatch" ||
    mode === "timer" ||
    mode === "stopwatch" ||
    badge === "timer" ||
    badge === "stopwatch" ||
    /\b(timer|stopwatch|countdown)\b/i.test(item.title) ||
    /\b(timer|stopwatch|countdown)\b/i.test(badge)
  );
};

export const isFileItem = (item: ActionCardItem): boolean => {
  const type = (item.type || "").toLowerCase();
  const tool = (item.payload?.tool || "").toLowerCase();
  const badge = (item.badge || "").toLowerCase();
  return (
    Boolean(item.payload?.file) ||
    type === "file" ||
    badge === "file" ||
    tool === "open_file" ||
    tool === "find_files" ||
    tool === "search_files"
  );
};

/**
 * Atmospheric Weather Icon with Micro-Animations
 */
const AnimatedWeatherIcon: React.FC<{ iconType?: string; isDark: boolean }> = ({ iconType = "sunny", isDark }) => {
  switch (iconType?.toLowerCase()) {
    case "rain":
      return (
        <div className="relative w-12 h-12 flex items-center justify-center">
          <CloudRain className="w-10 h-10 text-sky-400 animate-bounce" style={{ animationDuration: "2.5s" }} />
          <div className="absolute bottom-1 flex space-x-1">
            <span className="w-1 h-2 bg-blue-400 rounded-full animate-ping opacity-75" />
            <span className="w-1 h-2 bg-cyan-300 rounded-full animate-ping opacity-60" style={{ animationDelay: "0.2s" }} />
          </div>
        </div>
      );
    case "thunderstorm":
      return (
        <div className="relative w-12 h-12 flex items-center justify-center">
          <CloudLightning className="w-10 h-10 text-amber-400 animate-pulse" />
        </div>
      );
    case "snow":
      return (
        <div className="relative w-12 h-12 flex items-center justify-center">
          <Snowflake className="w-10 h-10 text-cyan-200 animate-spin" style={{ animationDuration: "8s" }} />
        </div>
      );
    case "cloudy":
    case "fog":
      return (
        <div className="relative w-12 h-12 flex items-center justify-center">
          <Cloud className="w-10 h-10 text-slate-300 animate-pulse" style={{ animationDuration: "3s" }} />
        </div>
      );
    case "partly-cloudy":
      return (
        <div className="relative w-12 h-12 flex items-center justify-center">
          <CloudSun className="w-10 h-10 text-amber-400" />
        </div>
      );
    case "sunny":
    default:
      return (
        <div className="relative w-12 h-12 flex items-center justify-center">
          <Sun className="w-10 h-10 text-amber-400 animate-spin" style={{ animationDuration: "16s" }} />
        </div>
      );
  }
};

/**
 * Dynamic Atmospheric Palette Styles computed directly from live meteorological condition
 */
function getAtmosphericPalette(iconType: string = "sunny", isDark: boolean) {
  switch (iconType?.toLowerCase()) {
    case "rain":
      return {
        bgGradient: isDark
          ? "from-slate-950/80 via-blue-950/70 to-slate-900/90"
          : "from-blue-100/90 via-sky-50/95 to-slate-100/90",
        borderColor: "border-sky-500/30",
        tempGradient: "from-sky-300 to-blue-200",
        glowColor: "rgba(56, 189, 248, 0.35)",
      };
    case "thunderstorm":
      return {
        bgGradient: isDark
          ? "from-indigo-950/80 via-purple-950/70 to-slate-950/90"
          : "from-indigo-100/90 via-purple-50/95 to-amber-50/90",
        borderColor: "border-purple-500/30",
        tempGradient: "from-amber-300 to-purple-300",
        glowColor: "rgba(168, 85, 247, 0.4)",
      };
    case "snow":
      return {
        bgGradient: isDark
          ? "from-cyan-950/80 via-slate-900/80 to-blue-950/70"
          : "from-cyan-50/95 via-sky-50/95 to-white",
        borderColor: "border-cyan-400/30",
        tempGradient: "from-cyan-200 to-white",
        glowColor: "rgba(103, 232, 249, 0.35)",
      };
    case "cloudy":
    case "fog":
      return {
        bgGradient: isDark
          ? "from-slate-900/85 via-gray-900/80 to-slate-950/90"
          : "from-slate-100/95 via-gray-50/95 to-slate-100/90",
        borderColor: "border-slate-500/30",
        tempGradient: "from-slate-200 to-sky-300",
        glowColor: "rgba(148, 163, 184, 0.3)",
      };
    case "partly-cloudy":
    case "sunny":
    default:
      return {
        bgGradient: isDark
          ? "from-sky-950/60 via-slate-900/90 to-blue-950/50"
          : "from-sky-100/90 via-amber-50/80 to-blue-50/90",
        borderColor: "border-sky-500/30",
        tempGradient: "from-sky-400 to-amber-300",
        glowColor: "rgba(56, 189, 248, 0.4)",
      };
  }
}

/**
 * Dynamic, Non-Hardcoded Meteorological Weather Card Widget
 */
const WeatherCardPalette: React.FC<{
  item: ActionCardItem;
  isDark: boolean;
  theme: any;
}> = ({ item, isDark, theme }) => {
  const [unit, setUnit] = useState<"C" | "F">("C");
  const [weatherData, setWeatherData] = useState<Record<string, any>>(() => item.payload || {});
  const [loading, setLoading] = useState<boolean>(item.payload?.temp_c == null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [isLiveTelemetry, setIsLiveTelemetry] = useState<boolean>(false);
  const [showSearch, setShowSearch] = useState<boolean>(false);
  const [searchInput, setSearchInput] = useState<string>("");

  const activeAbortRef = useRef<AbortController | null>(null);
  const initialCity = item.payload?.city || item.title?.replace(/^Weather in\s+/i, "") || "";

  const fetchLiveWeather = async (targetCity: string) => {
    if (activeAbortRef.current) {
      activeAbortRef.current.abort();
    }
    const controller = new AbortController();
    activeAbortRef.current = controller;

    setLoading(true);
    setFetchError(null);
    try {
      const url = targetCity ? `/api/weather?city=${encodeURIComponent(targetCity)}` : "/api/weather";
      const res = await fetch(url, { signal: controller.signal });
      if (res.ok) {
        const data = await res.json();
        if (data && typeof data === "object") {
          setWeatherData(data);
          setIsLiveTelemetry(true);
        }
      } else {
        setFetchError("Unable to retrieve weather telemetry");
      }
    } catch (e: any) {
      if (e?.name !== "AbortError") {
        setFetchError("Connection error while fetching weather");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (item.payload?.temp_c == null) {
      fetchLiveWeather(initialCity);
    }
    return () => {
      if (activeAbortRef.current) activeAbortRef.current.abort();
    };
  }, [initialCity]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchInput.trim()) {
      fetchLiveWeather(searchInput.trim());
      setShowSearch(false);
      setSearchInput("");
    }
  };

  const tempC = weatherData.temp_c ?? weatherData.temp_C ?? weatherData.temperature ?? "";
  const tempF = weatherData.temp_f ?? weatherData.temp_F ?? (tempC !== "" && !isNaN(Number(tempC)) ? Math.round((Number(tempC) * 9) / 5 + 32) : "");
  const feelsLike = weatherData.feels_like_c ?? weatherData.FeelsLikeC ?? tempC;
  const condition = weatherData.condition ?? weatherData.weatherDesc?.[0]?.value ?? "Meteorological Live Data";
  const humidity = weatherData.humidity ?? "--";
  const windKmph = weatherData.wind_kmph ?? weatherData.windspeedKmph ?? "--";
  const uvIndex = weatherData.uv_index ?? weatherData.uvIndex ?? "--";
  const city = weatherData.city || initialCity || "Local Area";
  const iconType = weatherData.icon_type || "sunny";

  const morningC = weatherData.morning_c ?? weatherData.morningC ?? tempC;
  const middayC = weatherData.midday_c ?? weatherData.middayC ?? tempC;
  const eveningC = weatherData.evening_c ?? weatherData.eveningC ?? tempC;

  const formatTemp = (val: any) => {
    if (val === "" || val === undefined || val === null || isNaN(Number(val))) return "--";
    return unit === "C" ? `${Math.round(Number(val))}°C` : `${Math.round((Number(val) * 9) / 5 + 32)}°F`;
  };

  const displayTemp = tempC === "" && loading
    ? "..."
    : unit === "C"
    ? `${tempC}°C`
    : `${tempF}°F`;

  const palette = getAtmosphericPalette(iconType, isDark);

  return (
    <div
      className={`relative overflow-hidden rounded-2xl p-4 sm:p-5 border transition-all shadow-xl bg-gradient-to-br ${palette.bgGradient} ${
        isDark ? "text-white" : "text-slate-900"
      }`}
      style={{
        borderColor: `${theme.primary}45`,
        boxShadow: `0 12px 32px rgba(0, 0, 0, 0.25), 0 0 24px ${palette.glowColor || theme.glow}`,
      }}
    >
      <div
        className="absolute -right-6 -top-6 w-32 h-32 rounded-full pointer-events-none opacity-25"
        style={{ background: `radial-gradient(circle, ${theme.primary}60 0%, transparent 70%)` }}
      />

      <div className="flex items-center justify-between mb-3 relative z-10">
        <div className="flex items-center space-x-2 min-w-0">
          <span className="relative flex h-2 w-2 flex-shrink-0">
            <span
              className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75"
              style={{ backgroundColor: theme.accent }}
            />
            <span
              className="relative inline-flex rounded-full h-2 w-2"
              style={{ backgroundColor: theme.primary }}
            />
          </span>
          <span className="text-xs font-semibold uppercase tracking-wider opacity-85 truncate">
            {city}
          </span>
          <span
            className="text-[10px] px-1.5 py-0.5 rounded-full font-mono border flex-shrink-0"
            style={{
              backgroundColor: `${theme.primary}20`,
              color: theme.accent,
              borderColor: `${theme.primary}40`,
            }}
          >
            {isLiveTelemetry ? "Live" : "Forecast"}
          </span>
        </div>

        <div className="flex items-center space-x-1.5">
          <button
            type="button"
            onClick={() => setShowSearch(!showSearch)}
            className="p-1 rounded-lg bg-white/10 hover:bg-white/20 transition-all border"
            style={{ borderColor: `${theme.primary}30` }}
            title="Search another city"
            aria-label="Search another city"
          >
            <Search className="w-3.5 h-3.5" />
          </button>

          <button
            type="button"
            onClick={() => fetchLiveWeather(city)}
            className={`p-1 rounded-lg bg-white/10 hover:bg-white/20 transition-all border ${
              loading ? "animate-spin" : ""
            }`}
            style={{ borderColor: `${theme.primary}30`, color: loading ? theme.accent : undefined }}
            title="Refresh live telemetry"
            aria-label="Refresh live telemetry"
          >
            <RotateCw className="w-3.5 h-3.5" />
          </button>

          <button
            type="button"
            onClick={() => setUnit(unit === "C" ? "F" : "C")}
            className="px-2 py-0.5 rounded-lg text-xs font-mono font-semibold bg-white/10 hover:bg-white/20 transition-all border"
            style={{ borderColor: `${theme.primary}30` }}
            title="Toggle Celsius / Fahrenheit"
            aria-label="Toggle Celsius or Fahrenheit"
          >
            °{unit}
          </button>
        </div>
      </div>

      {showSearch && (
        <form onSubmit={handleSearchSubmit} className="mb-3 relative z-10">
          <div className="flex items-center space-x-1.5">
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Enter city (e.g. Tokyo, London, Paris)..."
              autoFocus
              className={`w-full px-3 py-1.5 rounded-xl text-xs border outline-none transition-all ${
                isDark
                  ? "bg-black/40 text-white placeholder:text-white/40"
                  : "bg-white/80 text-slate-900 placeholder:text-black/40"
              }`}
              style={{ borderColor: `${theme.primary}40` }}
            />
            <button
              type="submit"
              className="px-3 py-1.5 rounded-xl text-xs font-semibold text-white transition-transform hover:scale-105 shadow-sm flex-shrink-0"
              style={{ background: theme.gradient }}
            >
              Go
            </button>
          </div>
        </form>
      )}

      {fetchError && (
        <div className="mb-2 p-2 rounded-lg bg-rose-500/15 border border-rose-500/30 text-rose-300 text-xs flex items-center space-x-1.5">
          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
          <span>{fetchError}</span>
        </div>
      )}

      <div className="flex items-center justify-between my-2 relative z-10">
        <div className="flex items-center space-x-3.5">
          <AnimatedWeatherIcon iconType={iconType} isDark={isDark} />
          <div>
            <div
              className="text-3xl sm:text-4xl font-bold tracking-tight bg-clip-text text-transparent"
              style={{
                backgroundImage: `linear-gradient(135deg, ${theme.accent} 0%, #f59e0b 100%)`,
              }}
            >
              {displayTemp}
            </div>
            <div className="text-xs sm:text-sm font-medium opacity-80 mt-0.5">
              {condition}
            </div>
          </div>
        </div>
      </div>

      <div
        className="grid grid-cols-4 gap-2 mt-3.5 pt-3 border-t relative z-10"
        style={{ borderColor: `${theme.primary}25` }}
      >
        <div
          className="flex flex-col items-center p-1.5 rounded-xl border transition-colors"
          style={{
            backgroundColor: `${theme.primary}12`,
            borderColor: `${theme.primary}28`,
          }}
        >
          <Droplets className="w-3.5 h-3.5 mb-1" style={{ color: theme.accent }} />
          <span className="text-[10px] opacity-60">Humidity</span>
          <span className="text-xs font-semibold">{humidity}{humidity !== "--" ? "%" : ""}</span>
        </div>

        <div
          className="flex flex-col items-center p-1.5 rounded-xl border transition-colors"
          style={{
            backgroundColor: `${theme.primary}12`,
            borderColor: `${theme.primary}28`,
          }}
        >
          <Wind className="w-3.5 h-3.5 mb-1" style={{ color: theme.accent }} />
          <span className="text-[10px] opacity-60">Wind</span>
          <span className="text-xs font-semibold">{windKmph}{windKmph !== "--" ? " km/h" : ""}</span>
        </div>

        <div
          className="flex flex-col items-center p-1.5 rounded-xl border transition-colors"
          style={{
            backgroundColor: `${theme.primary}12`,
            borderColor: `${theme.primary}28`,
          }}
        >
          <Sun className="w-3.5 h-3.5 mb-1 text-amber-400" />
          <span className="text-[10px] opacity-60">UV Index</span>
          <span className="text-xs font-semibold">{uvIndex}</span>
        </div>

        <div
          className="flex flex-col items-center p-1.5 rounded-xl border transition-colors"
          style={{
            backgroundColor: `${theme.primary}12`,
            borderColor: `${theme.primary}28`,
          }}
        >
          <Thermometer className="w-3.5 h-3.5 mb-1 text-rose-400" />
          <span className="text-[10px] opacity-60">Feels Like</span>
          <span className="text-xs font-semibold">{formatTemp(feelsLike)}</span>
        </div>
      </div>

      {tempC !== "" && (
        <div
          className="flex items-center justify-between mt-3 pt-2.5 border-t text-[11px] opacity-85 relative z-10 px-1"
          style={{ borderColor: `${theme.primary}25` }}
        >
          <div className="flex items-center space-x-1">
            <span>🌅 Morning</span>
            <span className="font-semibold">{formatTemp(morningC)}</span>
          </div>
          <div className="flex items-center space-x-1">
            <span>☀️ Midday</span>
            <span className="font-semibold">{formatTemp(middayC)}</span>
          </div>
          <div className="flex items-center space-x-1">
            <span>🌙 Evening</span>
            <span className="font-semibold">{formatTemp(eveningC)}</span>
          </div>
        </div>
      )}
    </div>
  );
};

/**
 * Live Countdown Timer & Stopwatch Widget with Drift-Free Timestamp Ticks
 */
const TimerCardPalette: React.FC<{
  item: ActionCardItem;
  isDark: boolean;
  theme: any;
}> = ({ item, isDark, theme }) => {
  const payload = item.payload || {};
  const isStopwatch =
    item.type === "stopwatch" ||
    payload.mode === "stopwatch" ||
    payload.tool === "stopwatch" ||
    /\bstopwatch\b/i.test(item.title) ||
    /\bstopwatch\b/i.test(payload.label || "");

  const initialDuration = Math.max(1, payload.duration_seconds || payload.seconds || 300);
  const timerTitle = payload.label || item.title?.replace(/^(?:Timer|Stopwatch):\s*/i, "") || (isStopwatch ? "Live Stopwatch" : "Countdown Timer");

  const [totalSeconds, setTotalSeconds] = useState<number>(initialDuration);
  const [remainingSeconds, setRemainingSeconds] = useState<number>(initialDuration);
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  const [isRunning, setIsRunning] = useState<boolean>(true);
  const [isCompleted, setIsCompleted] = useState<boolean>(false);

  // Target timestamp tracking to eliminate drift across background tab throttling
  const targetEndRef = useRef<number>(Date.now() + initialDuration * 1000);
  const stopwatchStartRef = useRef<number>(Date.now());

  useEffect(() => {
    if (!isRunning) return;

    if (isStopwatch) {
      stopwatchStartRef.current = Date.now() - elapsedSeconds * 1000;
    } else {
      targetEndRef.current = Date.now() + remainingSeconds * 1000;
    }

    const interval = setInterval(() => {
      if (isStopwatch) {
        const elapsed = Math.floor((Date.now() - stopwatchStartRef.current) / 1000);
        setElapsedSeconds(elapsed);
      } else {
        const remaining = Math.max(0, Math.ceil((targetEndRef.current - Date.now()) / 1000));
        setRemainingSeconds(remaining);
        if (remaining <= 0) {
          setIsRunning(false);
          setIsCompleted(true);
          try {
            sfx.playSuccess();
          } catch {}
        }
      }
    }, 500);

    return () => clearInterval(interval);
  }, [isRunning, isStopwatch]);

  const handleTogglePlayPause = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!isStopwatch && remainingSeconds === 0) {
      setRemainingSeconds(totalSeconds);
      targetEndRef.current = Date.now() + totalSeconds * 1000;
      setIsCompleted(false);
      setIsRunning(true);
    } else {
      setIsRunning(!isRunning);
    }
  };

  const handleReset = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsRunning(false);
    setIsCompleted(false);
    if (isStopwatch) {
      setElapsedSeconds(0);
      stopwatchStartRef.current = Date.now();
    } else {
      setRemainingSeconds(totalSeconds);
      targetEndRef.current = Date.now() + totalSeconds * 1000;
    }
  };

  const handleAddMinutes = (mins: number, e: React.MouseEvent) => {
    e.stopPropagation();
    const addedSecs = mins * 60;
    setTotalSeconds((prev) => prev + addedSecs);
    setRemainingSeconds((prev) => {
      const next = prev + addedSecs;
      targetEndRef.current = Date.now() + next * 1000;
      return next;
    });
    if (isCompleted) {
      setIsCompleted(false);
      setIsRunning(true);
    }
  };

  const formatTime = (secs: number) => {
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    if (h > 0) {
      return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  };

  const displayTime = isStopwatch ? formatTime(elapsedSeconds) : formatTime(remainingSeconds);
  const progressPercent = isStopwatch
    ? ((elapsedSeconds % 60) / 60)
    : totalSeconds > 0
    ? remainingSeconds / totalSeconds
    : 0;

  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference * (1 - progressPercent);

  const targetLabel = totalSeconds < 60
    ? `Target: ${totalSeconds} sec`
    : `Target: ${Math.round(totalSeconds / 60)} min`;

  return (
    <div
      className={`relative overflow-hidden rounded-2xl p-4 sm:p-5 border transition-all shadow-xl ${
        isCompleted
          ? "text-amber-200"
          : isDark
          ? "text-white"
          : "text-slate-900"
      }`}
      style={{
        background: isCompleted
          ? "linear-gradient(135deg, rgba(120, 53, 15, 0.75) 0%, rgba(136, 19, 55, 0.65) 100%)"
          : isDark
          ? `linear-gradient(135deg, rgba(15, 23, 42, 0.94) 0%, ${theme.primary}22 50%, rgba(10, 15, 30, 0.96) 100%)`
          : `linear-gradient(135deg, rgba(255, 255, 255, 0.96) 0%, ${theme.primary}14 50%, rgba(240, 245, 255, 0.94) 100%)`,
        borderColor: isCompleted ? "rgba(245, 158, 11, 0.45)" : `${theme.primary}45`,
        boxShadow: `0 12px 32px rgba(0, 0, 0, 0.25), 0 0 24px ${theme.glow}`,
      }}
    >
      <div
        className="absolute -right-6 -top-6 w-32 h-32 rounded-full pointer-events-none opacity-25"
        style={{
          background: isCompleted
            ? "radial-gradient(circle, #f59e0b 0%, #ef4444 60%, transparent 100%)"
            : `radial-gradient(circle, ${theme.primary} 0%, ${theme.accent} 60%, transparent 100%)`,
        }}
      />

      {/* Header */}
      <div className="flex items-center justify-between mb-3 relative z-10">
        <div className="flex items-center space-x-2">
          <span className="relative flex h-2 w-2">
            <span
              className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75"
              style={{ backgroundColor: isCompleted ? "#f59e0b" : theme.accent }}
            />
            <span
              className="relative inline-flex rounded-full h-2 w-2"
              style={{ backgroundColor: isCompleted ? "#d97706" : theme.primary }}
            />
          </span>
          <span className="text-xs font-semibold uppercase tracking-wider opacity-85">
            {timerTitle}
          </span>
          <span
            className="text-[10px] px-1.5 py-0.5 rounded-full font-mono border"
            style={{
              backgroundColor: isCompleted ? "rgba(245, 158, 11, 0.2)" : `${theme.primary}20`,
              color: isCompleted ? "#fcd34d" : theme.accent,
              borderColor: isCompleted ? "rgba(245, 158, 11, 0.4)" : `${theme.primary}40`,
            }}
          >
            {isCompleted ? "Time's Up!" : isRunning ? "Active" : "Paused"}
          </span>
        </div>

        {!isStopwatch && (
          <div className="flex items-center space-x-1">
            {[1, 5].map((m) => (
              <button
                key={m}
                type="button"
                onClick={(e) => handleAddMinutes(m, e)}
                className="px-2 py-0.5 rounded-lg text-xs font-semibold bg-white/10 hover:bg-white/20 transition-all border"
                style={{ borderColor: `${theme.primary}30` }}
              >
                +{m}m
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Main Timer Display */}
      <div className="flex items-center justify-between my-2 relative z-10">
        <div className="flex items-center space-x-4">
          <div className="relative w-24 h-24 flex items-center justify-center flex-shrink-0">
            <svg className="w-full h-full transform -rotate-90" viewBox="0 0 100 100">
              <circle
                cx="50"
                cy="50"
                r={radius}
                className={isDark ? "stroke-white/10" : "stroke-black/10"}
                strokeWidth="6"
                fill="transparent"
              />
              <circle
                cx="50"
                cy="50"
                r={radius}
                stroke={isCompleted ? "#f59e0b" : theme.accent || theme.primary}
                strokeWidth="6"
                strokeDasharray={circumference}
                strokeDashoffset={strokeDashoffset}
                strokeLinecap="round"
                fill="transparent"
                className="transition-[stroke-dashoffset] duration-1000 ease-linear"
              />
            </svg>
            <div className="absolute inset-0 flex items-center justify-center">
              {isCompleted ? (
                <AlarmClock className="w-8 h-8 text-amber-400 animate-bounce" />
              ) : isStopwatch ? (
                <Clock className="w-7 h-7 opacity-85" style={{ color: theme.accent }} />
              ) : (
                <Timer className="w-7 h-7 opacity-85" style={{ color: theme.accent }} />
              )}
            </div>
          </div>

          <div className="flex flex-col">
            <div className="text-3xl sm:text-4xl font-mono font-bold tracking-tight">
              {displayTime}
            </div>
            <div className="text-xs opacity-70 mt-0.5 font-medium">
              {isStopwatch
                ? isRunning
                  ? "Stopwatch Counting Up"
                  : "Stopwatch Paused"
                : isCompleted
                ? "Countdown Finished"
                : targetLabel}
            </div>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          <button
            type="button"
            onClick={handleTogglePlayPause}
            className="w-10 h-10 rounded-full flex items-center justify-center text-white shadow-lg transition-transform hover:scale-105 active:scale-95"
            style={{
              background: isCompleted
                ? "linear-gradient(135deg, #f59e0b, #d97706)"
                : theme.gradient,
            }}
            title={isRunning ? "Pause" : "Start"}
            aria-label={isRunning ? "Pause timer" : "Start timer"}
          >
            {isRunning ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
          </button>
          <button
            type="button"
            onClick={handleReset}
            className="w-10 h-10 rounded-full flex items-center justify-center bg-white/10 hover:bg-white/20 transition-all border border-white/15"
            title="Reset"
            aria-label="Reset timer"
          >
            <RotateCcw className="w-4 h-4 opacity-80" />
          </button>
        </div>
      </div>
    </div>
  );
};

export const ActionCard: React.FC<ActionCardProps> = ({
  items,
  onToggleItem,
  onExecuteSingleItem,
  onConfirm,
  onCancel,
  onRetry,
  isDark,
  colorTheme = "violet",
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;

  const getIcon = (item: ActionCardItem) => {
    const type = (item.type || "").toLowerCase();
    const badge = (item.badge || "").toLowerCase();
    const tool = (item.payload?.tool || "").toLowerCase();
    const title = (item.title || "").toLowerCase();

    // Media & Music
    if (
      type === "media" ||
      badge === "media" ||
      badge === "youtube" ||
      badge === "music" ||
      /\b(youtube|media|music|audio|song|track)\b/i.test(tool) ||
      /^play:\s*/i.test(title) ||
      /^playing\b/i.test(title)
    ) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-rose-500 via-pink-500 to-purple-600 flex items-center justify-center shadow-md shadow-rose-500/25 text-white flex-shrink-0">
          <Music className="w-4 h-4" />
        </div>
      );
    }

    // Weather & Meteorology
    if (isWeatherItem(item)) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-sky-500 via-cyan-500 to-amber-400 flex items-center justify-center shadow-md shadow-sky-500/25 text-white flex-shrink-0">
          <CloudSun className="w-4 h-4" />
        </div>
      );
    }

    // Timer & Stopwatch
    if (isTimerItem(item)) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-amber-500 via-orange-500 to-rose-500 flex items-center justify-center shadow-md shadow-amber-500/25 text-white flex-shrink-0">
          <Timer className="w-4 h-4" />
        </div>
      );
    }

    // File Operations & Folders (using exact boundaries to not hit "profile")
    if (isFileItem(item) || /\b(file|folder|dir|directory)\b/i.test(title)) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-blue-500 via-indigo-500 to-cyan-500 flex items-center justify-center shadow-md shadow-indigo-500/25 text-white flex-shrink-0">
          <FolderOpen className="w-4 h-4" />
        </div>
      );
    }

    // Reminders & Clock
    if (badge === "reminder" || tool === "reminder" || badge === "clock" || tool === "clock") {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-orange-500 to-rose-500 flex items-center justify-center shadow-md shadow-orange-500/25 text-white flex-shrink-0">
          <Clock className="w-4 h-4" />
        </div>
      );
    }

    // Web Search, Google & Browser (not blindly any item with URL)
    if (badge === "web" || badge === "google" || /\b(search|google_search|browse)\b/i.test(tool)) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-sky-500 via-blue-600 to-indigo-600 flex items-center justify-center shadow-md shadow-sky-500/25 text-white flex-shrink-0">
          <Search className="w-4 h-4" />
        </div>
      );
    }

    // Links & URLs
    if (type === "link" || (item.url && isSafeHttpUrl(item.url))) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-teal-500 via-emerald-500 to-cyan-600 flex items-center justify-center shadow-md shadow-teal-500/25 text-white flex-shrink-0">
          <Globe className="w-4 h-4" />
        </div>
      );
    }

    // System Settings
    if (badge === "settings" || /\bsettings\b/i.test(tool) || /\bsettings\b/i.test(title)) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-slate-600 via-slate-700 to-indigo-600 flex items-center justify-center shadow-md shadow-slate-600/25 text-white flex-shrink-0">
          <Settings className="w-4 h-4" />
        </div>
      );
    }

    // System Status & Hardware
    if (type === "device" || badge === "system" || badge === "hardware" || /\b(system|brightness|volume|cpu|ram)\b/i.test(tool)) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-amber-500 via-yellow-500 to-orange-500 flex items-center justify-center shadow-md shadow-amber-500/25 text-white flex-shrink-0">
          <Cpu className="w-4 h-4" />
        </div>
      );
    }

    // Messaging & Chat Notes
    if (type === "message" || badge === "message" || tool === "chat") {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-emerald-500 to-green-400 flex items-center justify-center shadow-md shadow-emerald-500/25 text-white flex-shrink-0">
          <MessageSquare className="w-4 h-4" />
        </div>
      );
    }

    // Restaurants & Food
    if (type === "restaurant" || badge === "food") {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-rose-500 to-orange-400 flex items-center justify-center shadow-md shadow-rose-500/25 text-white flex-shrink-0">
          <Utensils className="w-4 h-4" />
        </div>
      );
    }

    // Maps & Location
    if (type === "map" || badge === "map" || badge === "location") {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-blue-500 to-cyan-400 flex items-center justify-center shadow-md shadow-blue-500/25 text-white flex-shrink-0">
          <MapPin className="w-4 h-4" />
        </div>
      );
    }

    // Code & Scripts (exact whole word boundary to avoid "decode")
    if (type === "code" || badge === "code" || /\b(code|python|script|bash)\b/i.test(tool)) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-purple-600 to-indigo-500 flex items-center justify-center shadow-md shadow-purple-600/25 text-white flex-shrink-0">
          <Code2 className="w-4 h-4" />
        </div>
      );
    }

    // Calendar
    if (type === "calendar" || badge === "calendar" || /\bcalendar\b/i.test(tool)) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-purple-500 to-indigo-400 flex items-center justify-center shadow-md shadow-purple-500/25 text-white flex-shrink-0">
          <Calendar className="w-4 h-4" />
        </div>
      );
    }

    // Default: Dynamic Theme Accent Sparkles
    return (
      <div
        className="w-8 h-8 rounded-xl flex items-center justify-center shadow-md text-white flex-shrink-0"
        style={{ background: theme.gradient }}
      >
        <Sparkles className="w-4 h-4" />
      </div>
    );
  };

  const isWeatherCard = items.some(isWeatherItem);
  const isTimerCard = items.some(isTimerItem);
  const isFileCard = items.some(isFileItem);

  const cardTitle = isWeatherCard
    ? "Weather Forecast"
    : isTimerCard
    ? "Timer & Stopwatch"
    : isFileCard
    ? "Matching Files"
    : "Quick Actions";

  // Check if multiple items have checkboxes and are selectable
  const hasSelectableItems = items.some((i) => i.actionType !== "button" && !i.payload?.file && !isTimerItem(i) && !isWeatherItem(i));
  const selectedCount = items.filter((i) => i.selected).length;

  return (
    <motion.div
      id="fluent-action-card-container"
      variants={scaleFade}
      initial="hidden"
      animate="show"
      exit="exit"
      className="w-full max-w-xl mx-auto relative px-2 sm:px-4 gpu-accelerated"
    >
      <div
        className="absolute -inset-2 rounded-3xl opacity-35 pointer-events-none blur-xl"
        style={{
          background: `radial-gradient(circle, ${theme.primary}66 0%, ${theme.secondary}25 70%, transparent 100%)`,
          transform: "translateZ(0)",
        }}
      />

      <div
        className={`relative rounded-2xl sm:rounded-3xl p-4 sm:p-5 shadow-2xl transition-colors duration-150 border transform-gpu ${
          isDark
            ? "acrylic-glass text-slate-100 shadow-black/50"
            : "acrylic-glass-light text-slate-900 shadow-slate-300/50"
        }`}
        style={{
          borderColor: `${theme.primary}40`,
          boxShadow: isDark
            ? `0 20px 50px rgba(0,0,0,0.55), 0 0 35px ${theme.glow}`
            : `0 20px 40px rgba(0,0,0,0.08), 0 0 25px ${theme.glow}`,
        }}
      >
        {/* Card Header */}
        <div
          className="flex items-center justify-between mb-3.5 pb-2.5 border-b text-xs"
          style={{ borderColor: `${theme.primary}25` }}
        >
          <div className="flex items-center space-x-2">
            <div
              className="w-5 h-5 rounded-lg flex items-center justify-center text-white text-[10px] shadow-sm"
              style={{ background: theme.gradient }}
            >
              <Sparkles className="w-3.5 h-3.5" />
            </div>
            <span className="font-semibold text-xs tracking-wide opacity-90" style={{ color: theme.accent }}>
              {cardTitle}
            </span>
          </div>

          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              sfx.playClick();
              onCancel();
            }}
            className="p-1 rounded-lg opacity-60 hover:opacity-100 hover:bg-white/10 transition-all border border-transparent hover:border-white/10"
            style={{ color: theme.accent }}
            title="Close"
            aria-label="Close action card"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Action Items List */}
        <motion.div className="space-y-3 mb-4 max-h-[46vh] sm:max-h-[50vh] overflow-y-auto custom-scrollbar pr-0.5" variants={staggerContainer} initial="hidden" animate="show">
          {items.map((item) => {
            if (item.payload?.file) {
              const file = item.payload.file;
              const actionItem = (action: string): ActionCardItem => ({
                ...item,
                id: `${item.id}-${action}`,
                payload: { tool: action === "open" ? "open_file" : action === "reveal" ? "reveal_file" : "copy_file_path", action, path: file.path },
              });
              return (
                <motion.div key={item.id} variants={staggerItem} className={`rounded-xl border p-3 gpu-accelerated transition-colors duration-150 ${isDark ? "border-white/10 bg-white/[0.04]" : "border-black/10 bg-white/80"}`}>
                  <div className="flex min-w-0 items-start gap-3">
                    <FileText className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: theme.accent }} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold" title={file.name}>{file.name}</p>
                      <p className="truncate text-[11px] opacity-60" title={file.folder}>{file.folder}</p>
                      <p className="mt-1 text-[10px] opacity-50">{file.extension} · {new Date(file.modified).toLocaleString()}</p>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <button type="button" onClick={() => onExecuteSingleItem?.(actionItem("open"))} className="flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-semibold text-white" style={{ background: theme.gradient }}><FileText className="h-3 w-3" />Open</button>
                    <button type="button" onClick={() => onExecuteSingleItem?.(actionItem("reveal"))} className="flex items-center gap-1 rounded-md border border-white/15 px-2 py-1 text-[10px] font-semibold"><FolderOpen className="h-3 w-3" />Reveal</button>
                    <button type="button" onClick={() => onExecuteSingleItem?.(actionItem("copy"))} className="flex items-center gap-1 rounded-md border border-white/15 px-2 py-1 text-[10px] font-semibold"><Copy className="h-3 w-3" />Copy Path</button>
                  </div>
                </motion.div>
              );
            }

            if (isTimerItem(item)) {
              return (
                <motion.div key={item.id} variants={staggerItem} className="gpu-accelerated">
                  <TimerCardPalette item={item} isDark={isDark} theme={theme} />
                </motion.div>
              );
            }

            if (isWeatherItem(item)) {
              return (
                <motion.div key={item.id} variants={staggerItem} className="gpu-accelerated">
                  <WeatherCardPalette item={item} isDark={isDark} theme={theme} />
                </motion.div>
              );
            }

            return (
              <motion.div
                key={item.id}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                whileHover={{ scale: 1.01 }}
                whileTap={{ scale: 0.99 }}
                transition={{ duration: 0.15, ease: "easeOut" }}
                onClick={() => {
                  if (item.actionType === "button") return;
                  sfx.playClick();
                  onToggleItem(item.id);
                }}
                className={`flex items-center justify-between p-3 rounded-xl transition-colors duration-150 border transform-gpu ${
                  item.actionType === "button" ? "" : "cursor-pointer"
                } ${
                  item.selected
                    ? "shadow-sm"
                    : isDark
                    ? "bg-white/5 border-transparent opacity-60 hover:opacity-90"
                    : "bg-black/5 border-transparent opacity-60 hover:opacity-90"
                }`}
                style={item.selected ? {
                  backgroundColor: isDark ? `${theme.primary}18` : `${theme.primary}12`,
                  borderColor: `${theme.primary}50`,
                } : {}}
              >
                <div className="flex items-center space-x-3.5 min-w-0 pr-2 flex-1">
                  {getIcon(item)}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center space-x-2">
                      <span className="text-sm font-medium leading-snug truncate">{item.title}</span>
                      {item.badge && (
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded-full font-mono border"
                          style={{
                            backgroundColor: `${theme.primary}20`,
                            color: theme.accent,
                            borderColor: `${theme.primary}40`,
                          }}
                        >
                          {item.badge}
                        </span>
                      )}
                    </div>
                    {item.subtitle && (
                      <div className="text-xs opacity-65 truncate mt-0.5">{item.subtitle}</div>
                    )}
                  </div>
                </div>

                {/* Explicit Action Button or Valid Link */}
                {item.actionType === "button" ? (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      sfx.playClick();
                      if (onExecuteSingleItem) onExecuteSingleItem(item);
                    }}
                    className="px-2.5 py-1 rounded-lg text-xs font-semibold text-white shadow-sm transition-all hover:scale-105 active:scale-95"
                    style={{ background: theme.gradient }}
                  >
                    Execute
                  </button>
                ) : item.url && isSafeHttpUrl(item.url) ? (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="p-1.5 rounded-lg opacity-60 hover:opacity-100 hover:bg-white/10 transition-all"
                    style={{ color: theme.accent }}
                    title="Open external link"
                    aria-label={`Open external link for ${item.title}`}
                  >
                    <ExternalLink className="w-4 h-4" />
                  </a>
                ) : (
                  <motion.div
                    animate={{ scale: item.selected ? [1, 1.12, 1] : 1 }}
                    transition={{ duration: 0.15 }}
                    className={`w-5 h-5 rounded-full flex items-center justify-center transition-colors duration-150 transform-gpu ${
                      item.selected
                        ? "text-white shadow-sm"
                        : "border border-white/30 dark:border-white/20"
                    }`}
                    style={item.selected ? { backgroundColor: theme.primary } : {}}
                  >
                    {item.selected && <Check className="w-3.5 h-3.5 stroke-[2.5]" />}
                  </motion.div>
                )}
              </motion.div>
            );
          })}
        </motion.div>

        {/* Footer actions */}
        <div className="pt-2 space-y-2">
          {hasSelectableItems && selectedCount > 0 && (
            <button
              type="button"
              onClick={() => {
                sfx.playClick();
                onConfirm();
              }}
              className="w-full py-2.5 px-4 rounded-xl text-xs font-semibold text-white transition-all shadow-md active:scale-95 flex items-center justify-center space-x-1.5"
              style={{ background: theme.gradient }}
            >
              <Check className="w-4 h-4" />
              <span>Confirm ({selectedCount} selected)</span>
            </button>
          )}

          {onRetry && isFileCard && (
            <button
              type="button"
              onClick={onRetry}
              className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-white/10 px-4 py-2 text-xs font-semibold transition-colors hover:bg-white/10"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              <span>Retry search</span>
            </button>
          )}

          <button
            id="action-cancel-button"
            type="button"
            onClick={onCancel}
            className={`w-full py-2.5 px-4 rounded-xl text-xs font-semibold transition-colors duration-150 border active:scale-95 flex items-center justify-center space-x-1.5 ${
              isDark
                ? "bg-white/5 hover:bg-white/10 text-slate-300"
                : "bg-black/5 hover:bg-black/10 text-slate-700"
            }`}
            style={{
              borderColor: `${theme.primary}35`,
            }}
          >
            <span>Close</span>
          </button>
        </div>
      </div>
    </motion.div>
  );
};
