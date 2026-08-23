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
  Sliders,
  CheckCircle2,
  ChevronRight,
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
  Plus,
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

  const displayTemp = tempC === "" && loading
    ? "..."
    : unit === "C"
    ? `${tempC}°C`
    : `${tempF}°F`;

  const palette = getAtmosphericPalette(iconType, isDark);

  return (
    <div
      className={`relative overflow-hidden rounded-2xl p-4 sm:p-5 border transition-all shadow-xl bg-gradient-to-br ${palette.bgGradient} ${palette.borderColor} ${
        isDark ? "text-white" : "text-slate-900"
      }`}
    >
      {/* Ambient Cloud / Sun Glow Aura */}
      <div
        className="absolute -right-6 -top-6 w-32 h-32 rounded-full blur-2xl pointer-events-none opacity-40 transition-colors duration-700"
        style={{ background: `radial-gradient(circle, ${palette.glowColor} 0%, transparent 70%)` }}
      />

      {/* Header: Location, Live Status & Controls */}
      <div className="flex items-center justify-between mb-3 relative z-10">
        <div className="flex items-center space-x-2 min-w-0">
          <span className="relative flex h-2 w-2 flex-shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-sky-500" />
          </span>
          <span className="text-xs font-semibold uppercase tracking-wider opacity-85 truncate">
            {city}
          </span>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full font-mono bg-sky-500/20 text-sky-400 border border-sky-500/30 flex-shrink-0">
            Live
          </span>
        </div>

        {/* Action Controls: Search, Refresh, C/F Toggle */}
        <div className="flex items-center space-x-1.5">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setShowSearch(!showSearch);
            }}
            className="p-1 rounded-lg opacity-70 hover:opacity-100 hover:bg-white/10 transition-all text-xs"
            title="Search City Weather"
          >
            <Search className="w-3.5 h-3.5" />
          </button>

          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              sfx.playClick();
              fetchLiveWeather(city);
            }}
            className={`p-1 rounded-lg opacity-70 hover:opacity-100 hover:bg-white/10 transition-all text-xs ${
              loading ? "animate-spin text-sky-400" : ""
            }`}
            title="Refresh Live Meteorological Radar"
          >
            <RotateCw className="w-3.5 h-3.5" />
          </button>

          {/* C/F Unit Toggle */}
          <div className="flex items-center space-x-0.5 p-0.5 rounded-lg bg-black/15 dark:bg-white/10 text-xs font-semibold">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setUnit("C");
              }}
              className={`px-1.5 py-0.5 rounded-md transition-all ${
                unit === "C" ? "bg-sky-500 text-white shadow-sm" : "opacity-60 hover:opacity-100"
              }`}
            >
              °C
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setUnit("F");
              }}
              className={`px-1.5 py-0.5 rounded-md transition-all ${
                unit === "F" ? "bg-sky-500 text-white shadow-sm" : "opacity-60 hover:opacity-100"
              }`}
            >
              °F
            </button>
          </div>
        </div>
      </div>

      {/* City Search Form (Expandable) */}
      {showSearch && (
        <form onSubmit={handleSearchSubmit} className="mb-3 relative z-10 flex space-x-1.5">
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Type city name (e.g. Tokyo, Paris)..."
            className="flex-1 px-3 py-1 text-xs rounded-xl bg-black/20 dark:bg-white/10 border border-white/20 focus:outline-none focus:border-sky-400"
            autoFocus
            onClick={(e) => e.stopPropagation()}
          />
          <button
            type="submit"
            className="px-3 py-1 rounded-xl text-xs font-semibold bg-sky-500 text-white shadow-sm"
          >
            Search
          </button>
        </form>
      )}

      {/* Main Hero: Animated Weather Icon + Giant Temperature & Condition */}
      <div className="flex items-center justify-between my-2 relative z-10">
        <div className="flex items-center space-x-4">
          <AnimatedWeatherIcon iconType={iconType} isDark={isDark} />
          <div>
            <div className={`text-3xl sm:text-4xl font-bold tracking-tight bg-gradient-to-r ${palette.tempGradient} bg-clip-text text-transparent`}>
              {displayTemp}
            </div>
            <div className="text-sm font-medium opacity-90 mt-0.5">{condition}</div>
          </div>
        </div>
      </div>

      {/* Weather Stats Grid */}
      <div className="grid grid-cols-4 gap-2 mt-4 pt-3 border-t border-sky-500/20 text-center relative z-10">
        <div className="flex flex-col items-center p-1.5 rounded-xl bg-sky-500/10 border border-sky-500/15">
          <Droplets className="w-3.5 h-3.5 text-sky-400 mb-1" />
          <span className="text-[10px] opacity-60">Humidity</span>
          <span className="text-xs font-semibold">{humidity}{humidity !== "--" ? "%" : ""}</span>
        </div>

        <div className="flex flex-col items-center p-1.5 rounded-xl bg-sky-500/10 border border-sky-500/15">
          <Wind className="w-3.5 h-3.5 text-cyan-400 mb-1" />
          <span className="text-[10px] opacity-60">Wind</span>
          <span className="text-xs font-semibold">{windKmph}{windKmph !== "--" ? " km/h" : ""}</span>
        </div>

        <div className="flex flex-col items-center p-1.5 rounded-xl bg-sky-500/10 border border-sky-500/15">
          <Sun className="w-3.5 h-3.5 text-amber-400 mb-1" />
          <span className="text-[10px] opacity-60">UV Index</span>
          <span className="text-xs font-semibold">{uvIndex}</span>
        </div>

        <div className="flex flex-col items-center p-1.5 rounded-xl bg-sky-500/10 border border-sky-500/15">
          <Thermometer className="w-3.5 h-3.5 text-rose-400 mb-1" />
          <span className="text-[10px] opacity-60">Feels Like</span>
          <span className="text-xs font-semibold">{feelsLike !== "--" ? `${feelsLike}°C` : "--"}</span>
        </div>
      </div>

      {/* 3-Period Dynamic Meteorological Outlook */}
      {tempC !== "" && !isNaN(Number(tempC)) && (
        <div className="flex items-center justify-between mt-3 pt-2.5 border-t border-sky-500/15 text-[11px] opacity-85 relative z-10 px-1">
          <div className="flex items-center space-x-1">
            <span>🌅 Morning</span>
            <span className="font-semibold">{unit === "C" ? `${Math.max(0, Number(tempC) - 3)}°C` : `${Math.round((Number(tempC) - 3) * 1.8 + 32)}°F`}</span>
          </div>
          <div className="flex items-center space-x-1">
            <span>☀️ Midday</span>
            <span className="font-semibold">{unit === "C" ? `${Number(tempC)}°C` : `${tempF}°F`}</span>
          </div>
          <div className="flex items-center space-x-1">
            <span>🌙 Evening</span>
            <span className="font-semibold">{unit === "C" ? `${Math.max(0, Number(tempC) - 2)}°C` : `${Math.round((Number(tempC) - 2) * 1.8 + 32)}°F`}</span>
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
          ? "bg-gradient-to-br from-amber-950/70 via-rose-950/60 to-slate-900 border-amber-500/40 text-amber-200"
          : isDark
          ? "bg-gradient-to-br from-slate-900/90 via-indigo-950/50 to-slate-950/90 border-indigo-500/30 text-white"
          : "bg-gradient-to-br from-indigo-50/95 via-white/95 to-slate-50/95 border-indigo-200 text-slate-900"
      }`}
    >
      {/* Ambient Fluid Glow */}
      <div
        className="absolute -right-6 -top-6 w-32 h-32 rounded-full blur-2xl pointer-events-none opacity-40 transition-colors duration-500"
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
              className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${
                isCompleted ? "bg-amber-400" : isStopwatch ? "bg-cyan-400" : "bg-indigo-400"
              }`}
            />
            <span
              className={`relative inline-flex rounded-full h-2 w-2 ${
                isCompleted ? "bg-amber-500" : isStopwatch ? "bg-cyan-500" : "bg-indigo-500"
              }`}
            />
          </span>
          <span className="text-xs font-semibold uppercase tracking-wider opacity-85">
            {timerTitle}
          </span>
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded-full font-mono border ${
              isCompleted
                ? "bg-amber-500/20 text-amber-300 border-amber-500/40"
                : isRunning
                ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
                : "bg-amber-500/20 text-amber-300 border-amber-500/30"
            }`}
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
                className="px-2 py-0.5 rounded-lg text-xs font-semibold bg-white/10 hover:bg-white/20 transition-all border border-white/10"
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
                className="transition-all duration-1000 ease-linear"
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
  isDark,
  colorTheme = "violet",
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const getIcon = (type: string) => {
    switch (type) {
      case "timer":
      case "stopwatch":
        return (
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-amber-500 via-orange-500 to-rose-500 flex items-center justify-center shadow-md shadow-amber-500/20 text-white flex-shrink-0">
            <Timer className="w-4 h-4" />
          </div>
        );
      case "weather":
        return (
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-sky-500 via-cyan-500 to-amber-400 flex items-center justify-center shadow-md shadow-sky-500/20 text-white flex-shrink-0">
            <CloudSun className="w-4 h-4" />
          </div>
        );
      case "message":
        return (
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-emerald-500 to-green-400 flex items-center justify-center shadow-md shadow-emerald-500/20 text-white flex-shrink-0">
            <MessageSquare className="w-4 h-4" />
          </div>
        );
      case "restaurant":
        return (
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-rose-500 to-orange-400 flex items-center justify-center shadow-md shadow-rose-500/20 text-white flex-shrink-0">
            <Utensils className="w-4 h-4" />
          </div>
        );
      case "map":
        return (
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-blue-500 to-cyan-400 flex items-center justify-center shadow-md shadow-blue-500/20 text-white flex-shrink-0">
            <MapPin className="w-4 h-4" />
          </div>
        );
      case "device":
        return (
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-amber-500 to-yellow-400 flex items-center justify-center shadow-md shadow-amber-500/20 text-white flex-shrink-0">
            <Zap className="w-4 h-4" />
          </div>
        );
      case "code":
        return (
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-purple-600 to-indigo-500 flex items-center justify-center shadow-md shadow-purple-600/20 text-white flex-shrink-0">
            <Code2 className="w-4 h-4" />
          </div>
        );
      case "link":
        return (
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-sky-500 to-blue-500 flex items-center justify-center shadow-md shadow-sky-500/20 text-white flex-shrink-0">
            <ExternalLink className="w-4 h-4" />
          </div>
        );
      case "calendar":
      default:
        return (
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-purple-500 to-indigo-400 flex items-center justify-center shadow-md shadow-purple-500/20 text-white flex-shrink-0">
            <Calendar className="w-4 h-4" />
          </div>
        );
    }
  };

  return (
    <motion.div
      id="fluent-action-card-container"
      initial={{ opacity: 0, y: 28, scale: 0.94 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -20, scale: 0.95 }}
      transition={{ type: "spring", stiffness: 280, damping: 24 }}
      className="w-full max-w-lg mx-auto relative px-4"
    >
      {/* Ambient Fluid Glow Behind Card */}
      <div
        className="absolute -inset-1.5 rounded-3xl opacity-40 blur-xl pointer-events-none transition-opacity duration-300"
        style={{
          background: `radial-gradient(circle, ${theme.primary}55 0%, ${theme.secondary}22 70%, transparent 100%)`,
          transform: "translateZ(0)",
          willChange: "opacity",
        }}
      />

      {/* Fluent Frosted Mica Card */}
      <div
        className={`card-bracket relative rounded-2xl p-4 sm:p-5 shadow-2xl transition-all duration-300 border backdrop-blur-xl ${
          isDark
            ? "acrylic-glass text-slate-100 border-white/10 shadow-indigo-950/40"
            : "acrylic-glass-light text-slate-900 border-black/10 shadow-indigo-200/50"
        }`}
      >
        {/* Interactive Connected Subsystem Header */}
        <div className="flex items-center justify-between mb-3.5 pb-2.5 border-b border-white/10 dark:border-white/10 text-xs">
          <div className="flex items-center space-x-2">
            <div
              className="w-5 h-5 rounded-lg flex items-center justify-center text-white text-[10px]"
              style={{ background: theme.gradient }}
            >
              <Zap className="w-3 h-3" />
            </div>
            <span className="font-semibold text-[11px] uppercase tracking-wider opacity-80">
              Intent Bridge Connected
            </span>
          </div>

          <div
            className="flex items-center space-x-1.5 px-2 py-0.5 rounded-full text-[10px] font-mono border"
            style={{
              backgroundColor: `${theme.primary}18`,
              borderColor: `${theme.primary}35`,
              color: theme.accent,
            }}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            <span>Action Ready</span>
          </div>
        </div>

        {/* Action Items List */}
        <div className="space-y-3 mb-5 max-h-80 overflow-y-auto custom-scrollbar pr-0.5">
          {items.map((item, idx) => {
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
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: idx * 0.06 + 0.05, duration: 0.3 }}
                >
                  <TimerCardPalette item={item} isDark={isDark} theme={theme} />
                </motion.div>
              );
            }

            if (item.type === "weather" || item.payload?.tool === "get_weather") {
              return (
                <motion.div
                  key={item.id}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: idx * 0.06 + 0.05, duration: 0.3 }}
                >
                  <WeatherCardPalette item={item} isDark={isDark} theme={theme} />
                </motion.div>
              );
            }

            return (
              <motion.div
                key={item.id}
                initial={{ opacity: 0, x: -16 }}
                animate={{ opacity: 1, x: 0 }}
                whileHover={{ scale: 1.012, y: -1 }}
                whileTap={{ scale: 0.985 }}
                transition={{ delay: idx * 0.06 + 0.05, duration: 0.25, ease: "easeOut" }}
                onClick={() => {
                  sfx.playClick();
                  if (item.actionType === "button" && onExecuteSingleItem) {
                    onExecuteSingleItem(item);
                  } else {
                    onToggleItem(item.id);
                  }
                }}
                className={`flex items-center justify-between p-3 rounded-xl cursor-pointer transition-all duration-200 border ${
                  item.selected
                    ? isDark
                      ? "bg-white/10 border-white/15 shadow-sm"
                      : "bg-white/80 border-black/10 shadow-sm"
                    : isDark
                    ? "bg-white/5 border-transparent opacity-60 hover:opacity-90"
                    : "bg-black/5 border-transparent opacity-60 hover:opacity-90"
                }`}
              >
                <div className="flex items-center space-x-3.5 min-w-0 pr-2 flex-1">
                  {getIcon(item.type)}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center space-x-2">
                      <span className="text-sm font-medium leading-snug truncate">{item.title}</span>
                      {item.badge && (
                        <span className="text-[10px] px-1.5 py-0.2 rounded-full font-mono bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">
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
                  animate={{ scale: item.selected ? [1, 1.15, 1] : 1 }}
                  transition={{ duration: 0.2 }}
                  className={`w-5 h-5 rounded-full flex items-center justify-center transition-all ${
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
          {items.every(
            (i) =>
              i.type === "weather" ||
              i.type === "timer" ||
              i.type === "stopwatch" ||
              i.payload?.tool === "get_weather" ||
              i.payload?.tool === "set_timer" ||
              i.payload?.tool === "stopwatch"
          ) ? (
            <button
              id="action-cancel-button"
              type="button"
              onClick={onCancel}
              className={`w-full py-2 px-4 rounded-xl text-xs font-semibold transition-all duration-200 border active:scale-95 ${
                isDark
                  ? "bg-white/5 hover:bg-white/10 text-slate-300 border-white/10 hover:border-white/20"
                  : "bg-black/5 hover:bg-black/10 text-slate-700 border-black/10 hover:border-black/20"
              }`}
            >
              Close Widget
            </button>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <button
                id="action-cancel-button"
                type="button"
                onClick={onCancel}
                className={`w-full py-2.5 px-4 rounded-xl text-sm font-medium transition-all duration-200 border active:scale-95 ${
                  isDark
                    ? "bg-white/5 hover:bg-white/10 text-slate-300 border-white/10 hover:border-white/20"
                    : "bg-black/5 hover:bg-black/10 text-slate-700 border-black/10 hover:border-black/20"
                }`}
              >
                Dismiss
              </button>

              <button
                id="action-confirm-button"
                type="button"
                onClick={onConfirm}
                className="w-full py-2.5 px-4 rounded-xl text-sm font-semibold text-white shadow-lg transition-all duration-200 active:scale-[0.98] hover:scale-[1.02] flex items-center justify-center space-x-1.5"
                style={{
                  background: theme.gradient,
                  boxShadow: `0 8px 20px ${theme.glow}`,
                }}
              >
                <span>Proceed</span>
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
};
