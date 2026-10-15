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
  TrendingUp,
  TrendingDown,
  DollarSign,
  BarChart2,
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

export const isStockItem = (item: ActionCardItem): boolean => {
  const type = (item.type || "").toLowerCase();
  const tool = (item.payload?.tool || "").toLowerCase();
  const badge = (item.badge || "").toLowerCase();
  return (
    type === "stock" ||
    tool === "stock_quote" ||
    tool === "market_quote" ||
    badge === "stock" ||
    Boolean(item.payload?.symbol && item.payload?.price !== undefined) ||
    Boolean(item.payload?.tool === "stock_quote")
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
 * Dynamic, Real-Time Interactive Stock & Market Analytics Card Widget
 */
const StockCardPalette: React.FC<{
  item: ActionCardItem;
  isDark: boolean;
  theme: any;
}> = ({ item, isDark, theme }) => {
  const initialPayload = item.payload || {};
  const [data, setData] = useState<Record<string, any>>(initialPayload);
  const [timeframe, setTimeframe] = useState<"1D" | "5D" | "1M" | "1Y">("1D");
  const [loading, setLoading] = useState<boolean>(false);
  const [hoveredPoint, setHoveredPoint] = useState<{ x: number; y: number; val: number; index: number } | null>(null);

  const symbol = data.symbol || item.title?.match(/\(([^)]+)\)/)?.[1] || "STOCK";
  const name = data.name || item.title?.replace(/\s*\([^)]*\)/, "") || symbol;
  const price = typeof data.price === "number" ? data.price : parseFloat(data.price) || 0;
  const currency = data.currency === "USD" ? "$" : data.currency === "INR" ? "₹" : data.currency === "EUR" ? "€" : data.currency === "GBP" ? "£" : (data.currency || "$");
  const changePct = typeof data.change_pct === "number" ? data.change_pct : (data.change && data.previous_close ? (data.change / data.previous_close) * 100 : 0);
  const change = typeof data.change === "number" ? data.change : (data.previous_close ? price - data.previous_close : 0);
  const isPositive = changePct >= 0;

  const history: number[] = Array.isArray(data.history) && data.history.length > 0 
    ? data.history 
    : [price * 0.985, price * 0.992, price * 0.988, price * 1.002, price * 1.006, price];

  const fetchRange = async (tf: "1D" | "5D" | "1M" | "1Y") => {
    setTimeframe(tf);
    const rangeMap = { "1D": "1d", "5D": "5d", "1M": "1mo", "1Y": "1y" };
    setLoading(true);
    try {
      const res = await fetch(`/api/stock?symbol=${encodeURIComponent(symbol)}&range=${rangeMap[tf]}`);
      if (res.ok) {
        const json = await res.json();
        if (json && !json.error) {
          setData((prev) => ({ ...prev, ...json }));
        }
      }
    } catch (e) {
      console.error("Failed to load stock timeframe", e);
    } finally {
      setLoading(false);
    }
  };

  // Memoized SVG Chart Geometry & Coordinates for 60/120 FPS Rendering
  const width = 480;
  const height = 135;
  const padding = { top: 16, bottom: 20, left: 12, right: 12 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  const { minVal, maxVal, valRange, points, pathD, areaD } = React.useMemo(() => {
    const min = Math.min(...history);
    const max = Math.max(...history);
    const range = Math.max(max - min, 0.001);
    const pts = history.map((val, idx) => {
      const x = padding.left + (idx / Math.max(history.length - 1, 1)) * chartW;
      const y = padding.top + chartH - ((val - min) / range) * chartH;
      return { x, y, val, index: idx };
    });
    const pD = pts.length > 0
      ? pts.reduce((acc, pt, i) => (i === 0 ? `M ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}` : `${acc} L ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`), "")
      : "";
    const aD = pts.length > 0
      ? `${pD} L ${pts[pts.length - 1].x.toFixed(1)} ${(padding.top + chartH).toFixed(1)} L ${pts[0].x.toFixed(1)} ${(padding.top + chartH).toFixed(1)} Z`
      : "";
    return { minVal: min, maxVal: max, valRange: range, points: pts, pathD: pD, areaD: aD };
  }, [history, chartW, chartH]);

  const prevClose = typeof data.previous_close === "number" ? data.previous_close : null;
  const prevCloseY = prevClose && prevClose >= minVal && prevClose <= maxVal
    ? padding.top + chartH - ((prevClose - minVal) / valRange) * chartH
    : null;

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const targetIdx = Math.round(ratio * (points.length - 1));
    if (points[targetIdx]) {
      setHoveredPoint(points[targetIdx]);
    }
  };

  const currentDisplayPrice = hoveredPoint ? hoveredPoint.val : price;
  const currentDiff = hoveredPoint && prevClose ? hoveredPoint.val - prevClose : change;
  const currentDiffPct = hoveredPoint && prevClose ? (currentDiff / prevClose) * 100 : changePct;

  const formatLargeNum = (num?: number | null) => {
    if (!num) return "--";
    if (num >= 1e12) return `${(num / 1e12).toFixed(2)}T`;
    if (num >= 1e9) return `${(num / 1e9).toFixed(2)}B`;
    if (num >= 1e6) return `${(num / 1e6).toFixed(2)}M`;
    if (num >= 1e3) return `${(num / 1e3).toFixed(1)}K`;
    return num.toLocaleString();
  };

  const strokeColor = isPositive ? "#10b981" : "#f43f5e";
  const glowShadow = isPositive ? "rgba(16, 185, 129, 0.25)" : "rgba(244, 63, 94, 0.25)";

  return (
    <div
      className={`relative rounded-[22px] border p-5 overflow-hidden transform-gpu ${
        isDark
          ? "bg-slate-900/60 border-white/10"
          : "bg-white/80 border-slate-200/80"
      }`}
      style={{
        boxShadow: `0 8px 24px -6px ${glowShadow}`,
      }}
    >
      {/* Top Header Row */}
      <div className="flex items-start justify-between gap-3 relative z-10">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className="px-2.5 py-0.5 rounded-lg text-xs font-mono font-black tracking-wider uppercase shadow-sm"
              style={{
                backgroundColor: isDark ? "rgba(255, 255, 255, 0.08)" : "rgba(0, 0, 0, 0.06)",
                color: isDark ? theme.accent : theme.primary,
                border: `1px solid ${isDark ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.08)"}`,
              }}
            >
              {symbol}
            </span>
            <span className="text-xs opacity-60 font-semibold tracking-wide uppercase">
              {data.exchange || "EQUITY"}
            </span>
            {data.as_of && (
              <span className="text-[10px] opacity-45 font-medium ml-auto hidden sm:inline">
                {data.as_of}
              </span>
            )}
          </div>
          <h3 className={`text-base sm:text-lg font-bold tracking-tight truncate mt-1 ${isDark ? "text-white" : "text-slate-900"}`}>
            {name}
          </h3>
        </div>

        {/* Timeframe Selector Pills */}
        <div className="flex items-center gap-1 p-1 rounded-xl bg-black/10 dark:bg-white/5 border border-white/5">
          {(["1D", "5D", "1M", "1Y"] as const).map((tf) => (
            <button
              key={tf}
              type="button"
              onClick={() => fetchRange(tf)}
              className={`px-2 py-1 rounded-lg text-[10px] font-bold tracking-wider transition-all active:scale-95 ${
                timeframe === tf
                  ? "bg-white text-slate-900 dark:bg-white/90 dark:text-slate-950 shadow-md scale-105"
                  : isDark ? "opacity-60 hover:opacity-100 text-slate-300 hover:bg-white/5" : "opacity-60 hover:opacity-100 text-slate-600 hover:bg-black/5"
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      {/* Hero Price & Change Row */}
      <div className="mt-3 flex items-baseline gap-3 flex-wrap relative z-10">
        <div className="text-2xl sm:text-3xl font-black tracking-tight font-mono">
          {currency}{currentDisplayPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
        <div
          className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold font-mono tracking-tight shadow-sm ${
            isPositive
              ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
              : "bg-rose-500/15 text-rose-400 border border-rose-500/30"
          }`}
        >
          {isPositive ? <TrendingUp className="w-3.5 h-3.5 stroke-[2.5]" /> : <TrendingDown className="w-3.5 h-3.5 stroke-[2.5]" />}
          <span>{isPositive ? "+" : ""}{currentDiffPct.toFixed(2)}%</span>
          <span className="opacity-75 text-[11px]">({isPositive ? "+" : ""}{currency}{Math.abs(currentDiff).toFixed(2)})</span>
        </div>
        {hoveredPoint && (
          <span className="text-[11px] opacity-60 font-mono ml-auto">
            Interactive Probe
          </span>
        )}
      </div>

      {/* Interactive SVG Sparkline Chart */}
      <div className="mt-3 relative w-full h-[135px] select-none">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className={`w-full h-full overflow-visible transition-opacity duration-300 ${loading ? "opacity-40 animate-pulse" : "opacity-100"}`}
          preserveAspectRatio="none"
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoveredPoint(null)}
        >
          <defs>
            <linearGradient id={`stock-gradient-${item.id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={strokeColor} stopOpacity="0.32" />
              <stop offset="85%" stopColor={strokeColor} stopOpacity="0.02" />
              <stop offset="100%" stopColor={strokeColor} stopOpacity="0" />
            </linearGradient>
          </defs>

          {/* Previous Close Reference Line */}
          {prevCloseY !== null && (
            <g>
              <line
                x1={padding.left}
                y1={prevCloseY}
                x2={width - padding.right}
                y2={prevCloseY}
                stroke={isDark ? "rgba(255,255,255,0.2)" : "rgba(0,0,0,0.18)"}
                strokeDasharray="4 4"
                strokeWidth="1.2"
              />
              <text
                x={width - padding.right - 2}
                y={prevCloseY - 4}
                textAnchor="end"
                className="text-[9px] font-mono fill-slate-400 opacity-60 select-none"
              >
                Prev {currency}{prevClose?.toFixed(2)}
              </text>
            </g>
          )}

          {/* Area Fill */}
          {areaD && <path d={areaD} fill={`url(#stock-gradient-${item.id})`} />}

          {/* Main Price Line with Hardware Accelerated Neon Glow */}
          {pathD && (
            <>
              <path
                d={pathD}
                fill="none"
                stroke={strokeColor}
                strokeWidth="6"
                strokeLinecap="round"
                strokeLinejoin="round"
                opacity="0.22"
              />
              <path
                d={pathD}
                fill="none"
                stroke={strokeColor}
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </>
          )}

          {/* Hover Indicator Crosshair */}
          {hoveredPoint && (
            <g>
              <line
                x1={hoveredPoint.x}
                y1={padding.top}
                x2={hoveredPoint.x}
                y2={padding.top + chartH}
                stroke={isDark ? "rgba(255,255,255,0.4)" : "rgba(0,0,0,0.3)"}
                strokeDasharray="2 2"
                strokeWidth="1"
              />
              <circle
                cx={hoveredPoint.x}
                cy={hoveredPoint.y}
                r="5"
                fill={strokeColor}
                stroke="#fff"
                strokeWidth="2"
                className="filter drop-shadow-md"
              />
            </g>
          )}
        </svg>

        {/* Hovered Price Floating Tooltip */}
        {hoveredPoint && (
          <div
            className="absolute pointer-events-none transform -translate-x-1/2 -translate-y-full py-1 px-2.5 rounded-lg bg-slate-900/95 text-white border border-white/20 text-[11px] font-mono font-bold shadow-xl backdrop-blur-md z-20 flex items-center gap-1.5"
            style={{
              left: `${(hoveredPoint.x / width) * 100}%`,
              top: `${Math.max(10, (hoveredPoint.y / height) * 135 - 8)}px`,
            }}
          >
            <span style={{ color: strokeColor }}>●</span>
            <span>{currency}{hoveredPoint.val.toFixed(2)}</span>
          </div>
        )}
      </div>

      {/* Financial Key Metrics Grid */}
      <div
        className="mt-4 pt-3.5 border-t grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs relative z-10"
        style={{ borderColor: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)" }}
      >
        <div className="p-2 rounded-xl bg-white/[0.03] dark:bg-white/[0.02] border border-white/5">
          <div className="text-[10px] opacity-50 uppercase tracking-wider font-semibold">Day Range</div>
          <div className="font-mono font-bold mt-0.5 truncate text-[11px] sm:text-xs">
            {data.day_low ? `${currency}${data.day_low.toFixed(2)}` : "--"} - {data.day_high ? `${currency}${data.day_high.toFixed(2)}` : "--"}
          </div>
        </div>

        <div className="p-2 rounded-xl bg-white/[0.03] dark:bg-white/[0.02] border border-white/5">
          <div className="text-[10px] opacity-50 uppercase tracking-wider font-semibold">Prev Close</div>
          <div className="font-mono font-bold mt-0.5 text-[11px] sm:text-xs">
            {prevClose ? `${currency}${prevClose.toFixed(2)}` : "--"}
          </div>
        </div>

        <div className="p-2 rounded-xl bg-white/[0.03] dark:bg-white/[0.02] border border-white/5">
          <div className="text-[10px] opacity-50 uppercase tracking-wider font-semibold">Volume</div>
          <div className="font-mono font-bold mt-0.5 text-[11px] sm:text-xs">
            {formatLargeNum(data.volume)}
          </div>
        </div>

        <div className="p-2 rounded-xl bg-white/[0.03] dark:bg-white/[0.02] border border-white/5">
          <div className="text-[10px] opacity-50 uppercase tracking-wider font-semibold">52W Range</div>
          <div className="font-mono font-bold mt-0.5 truncate text-[11px] sm:text-xs">
            {data.fifty_two_week_low ? `${currency}${data.fifty_two_week_low.toFixed(1)}` : "--"} - {data.fifty_two_week_high ? `${currency}${data.fifty_two_week_high.toFixed(1)}` : "--"}
          </div>
        </div>
      </div>

      {/* External Chart Link Footer */}
      {data.url && (
        <div className="mt-3.5 pt-2.5 flex items-center justify-between text-[11px] opacity-75">
          <span className="font-medium">Live market data powered by Yahoo Finance</span>
          <a
            href={data.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="flex items-center gap-1 font-bold underline hover:opacity-100 transition-opacity"
            style={{ color: isDark ? theme.accent : theme.primary }}
          >
            <span>Full Chart</span>
            <ExternalLink className="w-3 h-3" />
          </a>
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

    // Stocks & Financial Markets
    if (isStockItem(item)) {
      const isUp = (item.payload?.change_pct ?? item.payload?.change ?? 0) >= 0;
      return (
        <div
          className={`w-8 h-8 rounded-xl bg-gradient-to-tr ${
            isUp
              ? "from-emerald-600 via-teal-500 to-green-400 shadow-emerald-500/25"
              : "from-rose-600 via-red-500 to-orange-400 shadow-rose-500/25"
          } flex items-center justify-center shadow-md text-white flex-shrink-0`}
        >
          {isUp ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
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
  const isStockCard = items.some(isStockItem);

  const cardTitle = isStockCard
    ? "Stock & Market Overview"
    : isWeatherCard
    ? "Weather Forecast"
    : isTimerCard
    ? "Timer & Stopwatch"
    : isFileCard
    ? "Matching Files"
    : "Quick Actions";

  // Check if multiple items have checkboxes and are selectable
  const hasSelectableItems = items.some(
    (i) => i.actionType !== "button" && !i.payload?.file && !isTimerItem(i) && !isWeatherItem(i) && !isStockItem(i)
  );
  const selectedCount = items.filter((i) => i.selected).length;

  const isWidgetCard = isStockCard || isWeatherCard || isTimerCard;

  return (
    <motion.div
      id="fluent-action-card-container"
      initial={{ opacity: 0, y: 14, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 8, scale: 0.98 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      style={{ willChange: "transform, opacity" }}
      className="w-full max-w-xl mx-auto relative px-2 sm:px-4 z-50 transform-gpu"
    >
      {/* Optimized Ambient Glow behind the card */}
      <div
        className="absolute -inset-2 rounded-3xl opacity-25 blur-xl pointer-events-none"
        style={{
          background: `radial-gradient(ellipse at top, ${theme.primary}66 0%, ${theme.secondary || theme.primary}33 45%, transparent 70%)`,
        }}
      />

      <div
        className={`relative rounded-[24px] sm:rounded-[32px] p-5 sm:p-7 shadow-2xl overflow-hidden backdrop-blur-xl border transform-gpu ${
          isDark
            ? "bg-slate-900/90 text-slate-100 shadow-black/60"
            : "bg-white/95 text-slate-900 shadow-slate-400/30"
        }`}
        style={{
          borderColor: isDark ? `${theme.primary}40` : `${theme.primary}25`,
        }}
      >
        {/* Inner subtle top highlight for depth */}
        <div className="absolute inset-x-0 top-0 h-[1px] bg-gradient-to-r from-transparent via-white/20 to-transparent" />

        {/* Card Header */}
        <div
          className="flex items-center justify-between mb-5 pb-4 border-b relative"
          style={{ borderColor: isDark ? `${theme.primary}30` : `${theme.primary}20` }}
        >
          <div className="flex items-center space-x-3">
            <div
              className="w-8 h-8 rounded-xl flex items-center justify-center text-white shadow-lg ring-1 ring-white/20"
              style={{ background: theme.gradient, boxShadow: `0 4px 14px ${theme.primary}50` }}
            >
              <Sparkles className="w-4 h-4" />
            </div>
            <span className="font-bold text-sm sm:text-base tracking-wide" style={{ color: isDark ? theme.accent : theme.primary }}>
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
            className="w-8 h-8 flex items-center justify-center rounded-full opacity-60 hover:opacity-100 hover:bg-slate-500/20 transition-colors active:scale-95"
            title="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Action Items List */}
        <motion.div
          className="space-y-3 mb-6 max-h-[46vh] sm:max-h-[50vh] overflow-y-auto custom-scrollbar pr-1"
          variants={isWidgetCard ? undefined : staggerContainer}
          initial={isWidgetCard ? undefined : "hidden"}
          animate={isWidgetCard ? undefined : "show"}
        >
          {items.map((item) => {
            if (item.payload?.file) {
              const file = item.payload.file;
              const actionItem = (action: string): ActionCardItem => ({
                ...item,
                id: `${item.id}-${action}`,
                payload: { tool: action === "open" ? "open_file" : action === "reveal" ? "reveal_file" : "copy_file_path", action, path: file.path },
              });
              return (
                <motion.div key={item.id} variants={staggerItem} className={`rounded-[18px] border p-4 transition-colors duration-150 shadow-sm ${isDark ? "border-white/10 bg-white/[0.03] hover:bg-white/[0.06]" : "border-black/5 bg-white hover:bg-slate-50"}`}>
                  <div className="flex min-w-0 items-start gap-3.5">
                    <div className="p-2 rounded-xl bg-slate-500/10">
                      <FileText className="h-5 w-5 flex-shrink-0" style={{ color: theme.accent }} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-bold" title={file.name}>{file.name}</p>
                      <p className="truncate text-xs opacity-60 mt-0.5" title={file.folder}>{file.folder}</p>
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button type="button" onClick={() => onExecuteSingleItem?.(actionItem("open"))} className="flex flex-1 justify-center items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold text-white shadow-md active:scale-95 transition-transform" style={{ background: theme.gradient }}><FileText className="h-3.5 w-3.5" />Open</button>
                    <button type="button" onClick={() => onExecuteSingleItem?.(actionItem("reveal"))} className="flex flex-1 justify-center items-center gap-1.5 rounded-xl border border-slate-500/30 px-3 py-2 text-xs font-semibold active:scale-95 transition-transform hover:bg-slate-500/10"><FolderOpen className="h-3.5 w-3.5" />Reveal</button>
                  </div>
                </motion.div>
              );
            }

            if (isTimerItem(item)) {
              return (
                <div key={item.id} className="transform-gpu">
                  <TimerCardPalette item={item} isDark={isDark} theme={theme} />
                </div>
              );
            }

            if (isWeatherItem(item)) {
              return (
                <div key={item.id} className="transform-gpu">
                  <WeatherCardPalette item={item} isDark={isDark} theme={theme} />
                </div>
              );
            }

            if (isStockItem(item)) {
              return (
                <div key={item.id} className="transform-gpu">
                  <StockCardPalette item={item} isDark={isDark} theme={theme} />
                </div>
              );
            }

            return (
              <motion.div
                key={item.id}
                variants={staggerItem}
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                onClick={() => {
                  if (item.actionType === "button") return;
                  sfx.playClick();
                  onToggleItem(item.id);
                }}
                className={`relative flex items-center justify-between p-3.5 sm:p-4 rounded-[18px] transition-all duration-200 border cursor-pointer overflow-hidden ${
                  item.selected
                    ? "shadow-md"
                    : isDark
                    ? "bg-white/5 border-white/5 hover:bg-white/10"
                    : "bg-white border-black/5 hover:bg-slate-50 shadow-sm"
                }`}
                style={item.selected ? {
                  backgroundColor: isDark ? `${theme.primary}25` : `${theme.primary}15`,
                  borderColor: `${theme.primary}60`,
                  boxShadow: `0 4px 15px ${theme.primary}30`,
                } : {}}
              >
                {/* Selection Highlight background */}
                {item.selected && (
                  <div className="absolute inset-0 opacity-20 pointer-events-none" style={{ background: `linear-gradient(90deg, ${theme.primary}, transparent)` }} />
                )}

                <div className="flex items-center space-x-4 min-w-0 pr-2 flex-1 relative z-10">
                  {getIcon(item)}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center space-x-2">
                      <span className="text-sm sm:text-base font-bold leading-snug truncate">{item.title}</span>
                      {item.badge && (
                        <span
                          className="text-[10px] px-2 py-0.5 rounded-full font-mono font-bold tracking-wide uppercase"
                          style={{
                            backgroundColor: `${theme.accent}20`,
                            color: theme.accent,
                          }}
                        >
                          {item.badge}
                        </span>
                      )}
                    </div>
                    {item.subtitle && (
                      <div className="text-xs font-medium opacity-60 truncate mt-0.5">{item.subtitle}</div>
                    )}
                  </div>
                </div>

                {/* Explicit Action Button or Valid Link */}
                <div className="relative z-10">
                  {item.actionType === "button" ? (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        sfx.playClick();
                        if (onExecuteSingleItem) onExecuteSingleItem(item);
                      }}
                      className="px-4 py-2 rounded-xl text-xs font-bold text-white shadow-md transition-all hover:scale-105 active:scale-95 hover:shadow-lg"
                      style={{ background: theme.gradient, boxShadow: `0 4px 12px ${theme.primary}40` }}
                    >
                      Execute
                    </button>
                  ) : item.url && isSafeHttpUrl(item.url) ? (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="w-10 h-10 flex items-center justify-center rounded-xl bg-slate-500/10 hover:bg-slate-500/20 transition-all text-slate-400 hover:text-slate-200"
                      title="Open external link"
                    >
                      <ExternalLink className="w-4 h-4" />
                    </a>
                  ) : (
                    <motion.div
                      animate={{ scale: item.selected ? [1, 1.2, 1] : 1 }}
                      transition={{ duration: 0.2 }}
                      className={`w-6 h-6 rounded-full flex items-center justify-center transition-all duration-200 ${
                        item.selected
                          ? "text-white shadow-md"
                          : "border-2 border-slate-500/30"
                      }`}
                      style={item.selected ? { backgroundColor: theme.primary, boxShadow: `0 2px 10px ${theme.primary}60` } : {}}
                    >
                      {item.selected && <Check className="w-3.5 h-3.5 stroke-[3]" />}
                    </motion.div>
                  )}
                </div>
              </motion.div>
            );
          })}
        </motion.div>

        {/* Footer actions */}
        <div className="pt-2 space-y-2.5 relative z-10">
          {hasSelectableItems && selectedCount > 0 && (
            <button
              type="button"
              onClick={() => {
                sfx.playClick();
                onConfirm();
              }}
              className="w-full py-3.5 px-4 rounded-xl text-sm font-bold text-white transition-all hover:scale-[1.02] shadow-lg active:scale-95 flex items-center justify-center space-x-2"
              style={{ background: theme.gradient, boxShadow: `0 8px 20px ${theme.primary}50` }}
            >
              <Check className="w-5 h-5 stroke-[2.5]" />
              <span>Confirm ({selectedCount} selected)</span>
            </button>
          )}

          {onRetry && isFileCard && (
            <button
              type="button"
              onClick={onRetry}
              className="flex w-full items-center justify-center gap-2 rounded-xl border border-slate-500/30 px-4 py-3 text-sm font-bold transition-all hover:bg-slate-500/10 active:scale-95"
            >
              <RotateCcw className="h-4 w-4" />
              <span>Retry Search</span>
            </button>
          )}

          <button
            id="action-cancel-button"
            type="button"
            onClick={onCancel}
            className={`w-full py-3 px-4 rounded-xl text-xs font-bold transition-all duration-200 border active:scale-95 flex items-center justify-center space-x-1.5 ${
              isDark
                ? "bg-slate-800/50 hover:bg-slate-700/60 text-slate-300 border-slate-700"
                : "bg-slate-100/50 hover:bg-slate-200/80 text-slate-700 border-slate-200"
            }`}
          >
            <span>Close Panel</span>
          </button>
        </div>
      </div>
    </motion.div>
  );
};
