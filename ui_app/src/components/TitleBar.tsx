import React from "react";
import {
  Sparkles,
  History,
  Settings,
} from "lucide-react";
import { ColorTheme, PluginMode, VisualizerMode } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { TextAnimationStyle } from "./KineticText";
import { sfx } from "../utils/audio";

interface TitleBarProps {
  isDark: boolean;
  onToggleTheme?: () => void;
  soundEnabled?: boolean;
  onToggleSound?: () => void;
  colorTheme: ColorTheme;
  onChangeColorTheme?: (theme: ColorTheme) => void;
  visualizerMode?: VisualizerMode;
  onChangeVisualizerMode?: (mode: VisualizerMode) => void;
  textAnimationStyle?: TextAnimationStyle;
  onChangeTextAnimationStyle?: (style: TextAnimationStyle) => void;
  onReset?: () => void;
  pluginMode: PluginMode;
  onChangePluginMode: (mode: PluginMode) => void;
  isOpen: boolean;
  onToggleOpen: () => void;
  showHistory: boolean;
  onToggleHistory: () => void;
  historyCount: number;
  showSettings?: boolean;
  onToggleSettings?: () => void;
  onOpenBackendSettings?: () => void;
  isBackendCustom?: boolean;
}

export const TitleBar: React.FC<TitleBarProps> = ({
  isDark,
  colorTheme,
  showHistory,
  onToggleHistory,
  historyCount,
  showSettings = false,
  onToggleSettings,
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const [isOnline, setIsOnline] = React.useState<boolean>(
    typeof navigator !== "undefined" && typeof navigator.onLine === "boolean"
      ? navigator.onLine
      : true
  );

  React.useEffect(() => {
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    const checkBackendStatus = async () => {
      try {
        const res = await fetch("/api/system-stats");
        if (res.ok) {
          const stats = await res.json();
          if (typeof stats.online === "boolean") {
            setIsOnline(stats.online);
          }
        }
      } catch (e) {}
    };

    checkBackendStatus();
    const interval = setInterval(checkBackendStatus, 10000);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      clearInterval(interval);
    };
  }, []);

  return (
    <header
      id="windows-11-titlebar"
      className={`relative z-50 flex items-center justify-between px-3.5 pt-[max(0.625rem,env(safe-area-inset-top))] pb-2.5 transition-colors duration-300 border-b backdrop-blur-xl select-none ${
        isDark
          ? "bg-black/25 border-white/[0.06] text-slate-200"
          : "bg-white/60 border-black/[0.05] text-slate-800"
      }`}
    >
      {/* Left: Brand Identity & Subsystem Live Radar */}
      <div className="flex items-center space-x-3">
        {/* App Brand Identity */}
        <div className="flex items-center space-x-2">
          <div
            className="w-6 h-6 rounded-lg flex items-center justify-center text-white shadow-sm transition-transform hover:scale-105"
            style={{ background: theme.gradient }}
          >
            <Sparkles className="w-3.5 h-3.5" />
          </div>
          <div className="flex items-center space-x-1.5">
            <span className="text-xs font-semibold tracking-tight">Amigo</span>
            {isOnline ? (
              <span className="text-[10px] px-1.5 py-0.2 rounded-full font-mono bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center space-x-1 transition-colors duration-200">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                <span>Online</span>
              </span>
            ) : (
              <span className="text-[10px] px-1.5 py-0.2 rounded-full font-mono bg-rose-500/15 text-rose-400 border border-rose-500/30 flex items-center space-x-1 transition-colors duration-200">
                <span className="w-1.5 h-1.5 rounded-full bg-rose-400 animate-pulse" />
                <span>Offline</span>
              </span>
            )}
          </div>
        </div>

        <span className="text-xs opacity-20 hidden sm:inline">|</span>

        {/* History Sidebar Toggle Button */}
        <button
          id="history-sidebar-toggle-btn"
          onClick={() => {
            sfx.playClick();
            onToggleHistory();
          }}
          className={`relative px-2.5 py-1 rounded-lg transition-all duration-200 flex items-center space-x-1.5 text-xs border ${
            showHistory
              ? "text-white shadow-sm font-semibold"
              : isDark
              ? "bg-white/[0.04] hover:bg-white/[0.08] border-white/[0.08] text-slate-300 hover:text-white"
              : "bg-black/[0.04] hover:bg-black/[0.08] border-black/[0.08] text-slate-700 hover:text-black"
          }`}
          style={showHistory ? { backgroundColor: theme.primary, borderColor: theme.primary } : {}}
          title="Toggle Command History Panel"
        >
          <History className="w-3.5 h-3.5" style={{ color: showHistory ? "#fff" : theme.accent }} />
          <span className="hidden sm:inline">History</span>
          {historyCount > 0 && (
            <span
              className={`text-[10px] font-bold px-1.5 py-0.2 rounded-full ${
                showHistory ? "bg-white/25 text-white" : isDark ? "bg-white/10 text-slate-300" : "bg-black/10 text-slate-700"
              }`}
            >
              {historyCount}
            </span>
          )}
        </button>
      </div>

      {/* Right Controls: Settings Button */}
      <div className="flex items-center space-x-2">
        <button
          id="settings-page-toggle-btn"
          onClick={() => {
            sfx.playClick();
            onToggleSettings?.();
          }}
          className={`relative px-2.5 py-1 rounded-lg transition-all duration-200 flex items-center space-x-1.5 text-xs border ${
            showSettings
              ? "text-white shadow-sm font-semibold"
              : isDark
              ? "bg-white/[0.04] hover:bg-white/[0.08] border-white/[0.08] text-slate-300 hover:text-white"
              : "bg-black/[0.04] hover:bg-black/[0.08] border-black/[0.08] text-slate-700 hover:text-black"
          }`}
          style={showSettings ? { backgroundColor: theme.primary, borderColor: theme.primary } : {}}
          title="Toggle Assistant Settings"
        >
          <Settings className="w-3.5 h-3.5" style={{ color: showSettings ? "#fff" : theme.accent }} />
          <span className="hidden sm:inline">Settings</span>
        </button>
      </div>
    </header>
  );
};
