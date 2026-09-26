import React, { useState, useEffect } from "react";
import { motion } from "motion/react";
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
 * 100% Dynamic, Non-Hardcoded Meteorological Weather Card Palette
 */
const WeatherCardPalette: React.FC<{
  item: ActionCardItem;
  isDark: boolean;
  theme: any;
}> = ({ item, isDark, theme }) => {
  const [unit, setUnit] = useState<"C" | "F">("C");
  const [weatherData, setWeatherData] = useState<Record<string, any>>(() => item.payload || {});
  const [loading, setLoading] = useState<boolean>(!item.payload?.temp_c);
  const [showSearch, setShowSearch] = useState<boolean>(false);
  const [searchInput, setSearchInput] = useState<string>("");

  const initialCity = item.payload?.city || item.title?.replace(/^Weather in\s+/i, "") || "";

  const fetchLiveWeather = async (targetCity: string) => {
    setLoading(true);
    try {
      const url = targetCity ? `/api/weather?city=${encodeURIComponent(targetCity)}` : "/api/weather";
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        if (data && typeof data === "object") {
          setWeatherData(data);
        }
      }
    } catch (e) {
      console.warn("Live weather fetch failed:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!item.payload?.temp_c) {
      fetchLiveWeather(initialCity);
    }
  }, [initialCity]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchInput.trim()) {
      fetchLiveWeather(searchInput.trim());
      setShowSearch(false);
      setSearchInput("");
    }
  };

  const tempC = weatherData.temp_c ?? weatherData.temp_C ?? "";
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
    if (val === "" || val === undefined || isNaN(Number(val))) return "--";
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
      className={`relative overflow-hidden rounded-2xl p-4 sm:p-5 border transition-all shadow-xl ${
        isDark ? "text-white" : "text-slate-900"
      }`}
      style={{
        background: isDark
          ? `linear-gradient(135deg, rgba(15, 23, 42, 0.94) 0%, ${theme.primary}22 50%, rgba(10, 15, 30, 0.96) 100%)`
          : `linear-gradient(135deg, rgba(255, 255, 255, 0.96) 0%, ${theme.primary}14 50%, rgba(240, 245, 255, 0.94) 100%)`,
        borderColor: `${theme.primary}45`,
        boxShadow: `0 12px 32px rgba(0, 0, 0, 0.25), 0 0 24px ${theme.glow}`,
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
            Live
          </span>
        </div>

        <div className="flex items-center space-x-1.5">
          <button
            type="button"
            onClick={() => setShowSearch(!showSearch)}
            className="p-1 rounded-lg bg-white/10 hover:bg-white/20 transition-all border"
            style={{ borderColor: `${theme.primary}30` }}
            title="Search another city"
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
          >
            <RotateCw className="w-3.5 h-3.5" />
          </button>

          <button
            type="button"
            onClick={() => setUnit(unit === "C" ? "F" : "C")}
            className="px-2 py-0.5 rounded-lg text-xs font-mono font-semibold bg-white/10 hover:bg-white/20 transition-all border"
            style={{ borderColor: `${theme.primary}30` }}
            title="Toggle Celsius / Fahrenheit"
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
          <span className="text-xs font-semibold">{feelsLike !== "--" ? `${feelsLike}°C` : "--"}</span>
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
 * Live Countdown Timer & Stopwatch Palette Widget
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
    /stopwatch/i.test(item.title) ||
    /stopwatch/i.test(payload.label || "");

  const initialDuration = Math.max(1, payload.duration_seconds || payload.seconds || 300);
  const timerTitle = payload.label || item.title?.replace(/^(?:Timer|Stopwatch):\s*/i, "") || (isStopwatch ? "Live Stopwatch" : "Countdown Timer");

  // Timer states
  const [totalSeconds, setTotalSeconds] = useState<number>(initialDuration);
  const [remainingSeconds, setRemainingSeconds] = useState<number>(initialDuration);
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  const [isRunning, setIsRunning] = useState<boolean>(true);
  const [isCompleted, setIsCompleted] = useState<boolean>(false);

  useEffect(() => {
    let interval: any = null;
    if (isRunning) {
      interval = setInterval(() => {
        if (isStopwatch) {
          setElapsedSeconds((prev) => prev + 1);
        } else {
          setRemainingSeconds((prev) => {
            if (prev <= 1) {
              setIsRunning(false);
              setIsCompleted(true);
              return 0;
            }
            return prev - 1;
          });
        }
      }, 1000);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [isRunning, isStopwatch]);

  const handleTogglePlayPause = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!isStopwatch && remainingSeconds === 0) {
      setRemainingSeconds(totalSeconds);
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
    } else {
      setRemainingSeconds(totalSeconds);
    }
  };

  const handleAddMinutes = (mins: number, e: React.MouseEvent) => {
    e.stopPropagation();
    const addedSecs = mins * 60;
    setTotalSeconds((prev) => prev + addedSecs);
    setRemainingSeconds((prev) => prev + addedSecs);
    if (isCompleted) {
      setIsCompleted(false);
      setIsRunning(true);
    }
  };

  const formatTime = (secs: number) => {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  };

  const displayTime = isStopwatch ? formatTime(elapsedSeconds) : formatTime(remainingSeconds);
  const progressPercent = isStopwatch
    ? ((elapsedSeconds % 60) / 60)
    : totalSeconds > 0
    ? remainingSeconds / totalSeconds
    : 0;

  const radius = 42;
  const circumference = 2 * Math.PI * radius; // ~263.89
  const strokeDashoffset = circumference * (1 - progressPercent);

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
      {/* Ambient Fluid Glow */}
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

        {/* Quick Add Mins (Timer mode only) */}
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

      {/* Main Timer / Stopwatch Display with Circular SVG Progress Ring */}
      <div className="flex items-center justify-between my-2 relative z-10">
        <div className="flex items-center space-x-4">
          {/* Progress Ring */}
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
                className="transition-[stroke-dashoffset] duration-500 ease-linear"
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

          {/* Time text & Status */}
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
                : `Target: ${Math.round(totalSeconds / 60)} min`}
            </div>
          </div>
        </div>

        {/* Action Controls */}
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
          >
            {isRunning ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
          </button>
          <button
            type="button"
            onClick={handleReset}
            className="w-10 h-10 rounded-full flex items-center justify-center bg-white/10 hover:bg-white/20 transition-all border border-white/15"
            title="Reset"
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

    // Media, Music & YouTube Audio/Video
    if (
      type === "media" ||
      badge === "media" ||
      badge === "youtube" ||
      badge === "music" ||
      tool.includes("youtube") ||
      tool.includes("media") ||
      title.startsWith("play:") ||
      title.startsWith("playing")
    ) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-rose-500 via-pink-500 to-purple-600 flex items-center justify-center shadow-md shadow-rose-500/25 text-white flex-shrink-0">
          <Music className="w-4 h-4" />
        </div>
      );
    }

    // Weather & Meteorology
    if (type === "weather" || badge === "weather" || tool.includes("weather") || title.includes("weather")) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-sky-500 via-cyan-500 to-amber-400 flex items-center justify-center shadow-md shadow-sky-500/25 text-white flex-shrink-0">
          <CloudSun className="w-4 h-4" />
        </div>
      );
    }

    // Timer & Stopwatch
    if (type === "timer" || type === "stopwatch" || badge === "timer" || badge === "stopwatch" || tool.includes("timer")) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-amber-500 via-orange-500 to-rose-500 flex items-center justify-center shadow-md shadow-amber-500/25 text-white flex-shrink-0">
          <Timer className="w-4 h-4" />
        </div>
      );
    }

    // Reminders & Clock
    if (badge === "reminder" || tool.includes("reminder") || badge === "clock" || tool.includes("time")) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-orange-500 to-rose-500 flex items-center justify-center shadow-md shadow-orange-500/25 text-white flex-shrink-0">
          <Clock className="w-4 h-4" />
        </div>
      );
    }

    // File Operations & Folders
    if (type === "file" || badge === "file" || badge === "folder" || tool.includes("file") || tool.includes("folder") || title.includes("file")) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-blue-500 via-indigo-500 to-cyan-500 flex items-center justify-center shadow-md shadow-indigo-500/25 text-white flex-shrink-0">
          <FolderOpen className="w-4 h-4" />
        </div>
      );
    }

    // Web Search, Google, Browser & Links
    if (type === "link" || badge === "web" || badge === "google" || tool.includes("search") || tool.includes("google") || item.url) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-sky-500 via-blue-600 to-indigo-600 flex items-center justify-center shadow-md shadow-sky-500/25 text-white flex-shrink-0">
          <Search className="w-4 h-4" />
        </div>
      );
    }

    // System Settings
    if (badge === "settings" || tool.includes("settings") || title.includes("settings")) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-slate-600 via-slate-700 to-indigo-600 flex items-center justify-center shadow-md shadow-slate-600/25 text-white flex-shrink-0">
          <Settings className="w-4 h-4" />
        </div>
      );
    }

    // System Status, CPU & Hardware
    if (type === "device" || badge === "system" || badge === "hardware" || tool.includes("system") || tool.includes("brightness") || tool.includes("volume")) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-amber-500 via-yellow-500 to-orange-500 flex items-center justify-center shadow-md shadow-amber-500/25 text-white flex-shrink-0">
          <Cpu className="w-4 h-4" />
        </div>
      );
    }

    // Message & Conversational Notes
    if (type === "message" || badge === "message" || tool.includes("chat")) {
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

    // Code & Scripts
    if (type === "code" || badge === "code" || tool.includes("code")) {
      return (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-purple-600 to-indigo-500 flex items-center justify-center shadow-md shadow-purple-600/25 text-white flex-shrink-0">
          <Code2 className="w-4 h-4" />
        </div>
      );
    }

    // Calendar
    if (type === "calendar" || badge === "calendar" || tool.includes("calendar")) {
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

  const isWeatherCard = items.some(i => i.type === "weather" || i.payload?.tool === "get_weather");
  const isTimerCard = items.some(i => i.type === "timer" || i.type === "stopwatch" || i.payload?.tool === "set_timer" || i.payload?.tool === "stopwatch");
  const isFileCard = items.some(i => i.payload?.file || i.type === "file");

  const cardTitle = isWeatherCard
    ? "Weather Forecast"
    : isTimerCard
    ? "Timer & Stopwatch"
    : isFileCard
    ? "Matching Files"
    : "Quick Actions";

  return (
    <motion.div
      id="fluent-action-card-container"
      initial={{ opacity: 0, y: 18, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -14, scale: 0.98 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      className="w-full max-w-xl mx-auto relative px-2 sm:px-4 transform-gpu will-change-[transform,opacity]"
    >
      {/* Ambient Fluid Glow Behind Card */}
      <div
        className="absolute -inset-2 rounded-3xl opacity-35 pointer-events-none blur-xl"
        style={{
          background: `radial-gradient(circle, ${theme.primary}66 0%, ${theme.secondary}25 70%, transparent 100%)`,
          transform: "translateZ(0)",
        }}
      />

      {/* Fluent Frosted Mica Card */}
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
        {/* Clean Header with Title & Close Button */}
        <div
          className="flex items-center justify-between mb-3.5 pb-2.5 border-b text-xs"
          style={{ borderColor: `${theme.primary}25` }}
        >
          <div className="flex items-center space-x-2">
            <div
              className="w-5 h-5 rounded-lg flex items-center justify-center text-white text-[10px] shadow-sm"
              style={{ background: theme.gradient }}
            >
              <Sparkles className="w-3 h-3" />
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
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Action Items List */}
        <div className="space-y-3 mb-4 max-h-[46vh] sm:max-h-[50vh] overflow-y-auto custom-scrollbar pr-0.5">
          {items.map((item, idx) => {
            if (item.payload?.file) {
              const file = item.payload.file;
              const actionItem = (action: string): ActionCardItem => ({
                ...item,
                id: `${item.id}-${action}`,
                payload: { tool: action === "open" ? "open_file" : action === "reveal" ? "reveal_file" : "copy_file_path", action, path: file.path },
              });
              return (
                <motion.div key={item.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.16, ease: "easeOut" }} className={`rounded-xl border p-3 transform-gpu transition-colors duration-150 ${isDark ? "border-white/10 bg-white/[0.04]" : "border-black/10 bg-white/80"}`}>
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
            if (
              item.type === "timer" ||
              item.type === "stopwatch" ||
              item.payload?.tool === "set_timer" ||
              item.payload?.tool === "stopwatch" ||
              item.payload?.mode === "stopwatch" ||
              item.payload?.mode === "timer" ||
              /timer|stopwatch|countdown/i.test(item.title) ||
              /timer|stopwatch|countdown/i.test(item.badge || "")
            ) {
              return (
                <motion.div
                  key={item.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.16, ease: "easeOut" }}
                  className="transform-gpu"
                >
                  <TimerCardPalette item={item} isDark={isDark} theme={theme} />
                </motion.div>
              );
            }

            if (item.type === "weather" || item.payload?.tool === "get_weather") {
              return (
                <motion.div
                  key={item.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.16, ease: "easeOut" }}
                  className="transform-gpu"
                >
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
                  sfx.playClick();
                  if (item.actionType === "button" && onExecuteSingleItem) {
                    onExecuteSingleItem(item);
                  } else {
                    onToggleItem(item.id);
                  }
                }}
                className={`flex items-center justify-between p-3 rounded-xl cursor-pointer transition-colors duration-150 border transform-gpu ${
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

              {/* Action Button or Checkbox Toggle Indicator */}
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
              ) : item.url ? (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="p-1.5 rounded-lg opacity-60 hover:opacity-100 hover:bg-white/10 transition-all"
                  style={{ color: theme.accent }}
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
        </div>

        {/* Buttons Footer */}
        <div className="pt-2">
          {onRetry && isFileCard && (
            <button type="button" onClick={onRetry} className="mb-2 flex w-full items-center justify-center gap-1.5 rounded-xl border border-white/10 px-4 py-2 text-xs font-semibold transition-colors hover:bg-white/10">
              <RotateCcw className="h-3.5 w-3.5" />Retry search
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
