import React from "react";
import {
  Sparkles,
  History,
  Settings,
  Server,
} from "lucide-react";
import { ColorTheme, PluginMode, VisualizerMode } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { TextAnimationStyle } from "./KineticText";
import { sfx } from "../utils/audio";

export interface TitleBarProps {
  isDark: boolean;
  colorTheme: ColorTheme;
  showHistory: boolean;
  onToggleHistory: () => void;
  historyCount: number;
  showSettings?: boolean;
  onToggleSettings?: () => void;
  onOpenBackendSettings?: () => void;
  isBackendCustom?: boolean;
  // Legacy / optional props kept for backward-compatible interface calls
  onToggleTheme?: () => void;
  soundEnabled?: boolean;
  onToggleSound?: () => void;
  onChangeColorTheme?: (theme: ColorTheme) => void;
  visualizerMode?: VisualizerMode;
  onChangeVisualizerMode?: (mode: VisualizerMode) => void;
  textAnimationStyle?: TextAnimationStyle;
  onChangeTextAnimationStyle?: (style: TextAnimationStyle) => void;
  onReset?: () => void;
  pluginMode?: PluginMode;
  onChangePluginMode?: (mode: PluginMode) => void;
  isOpen?: boolean;
  onToggleOpen?: () => void;
}

export const TitleBar: React.FC<TitleBarProps> = ({
  isDark,
  colorTheme,
  showHistory,
  onToggleHistory,
  historyCount,
  showSettings = false,
  onToggleSettings,
  onOpenBackendSettings,
  isBackendCustom = false,
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const [isOnline, setIsOnline] = React.useState<boolean>(true);

  React.useEffect(() => {
    let isSubscribed = true;
    let abortController: AbortController | null = null;

    // Listen to real-time SSE stream status events from App.tsx
    const handleBackendStatus = (e: Event) => {
      const customEvent = e as CustomEvent<{ online: boolean }>;
      if (typeof customEvent.detail?.online === "boolean") {
        setIsOnline(customEvent.detail.online);
      }
    };
    window.addEventListener("amigo_backend_status", handleBackendStatus);

    const checkBackendStatus = async () => {
      // Don't poll in background/hidden tabs
      if (typeof document !== "undefined" && document.hidden) return;

      if (abortController) {
        abortController.abort();
      }
      abortController = new AbortController();
      const timeoutId = setTimeout(() => {
        try {
          abortController?.abort();
        } catch {}
      }, 3500);

      try {
        const res = await fetch("/api/system-stats", { signal: abortController.signal });
        clearTimeout(timeoutId);
        if (!isSubscribed) return;
        if (res.ok) {
          const stats = await res.json();
          setIsOnline(stats.online !== false);
        } else {
          setIsOnline(false);
        }
      } catch (e: any) {
        clearTimeout(timeoutId);
        if (!isSubscribed) return;
        if (e?.name !== "AbortError") {
          setIsOnline(false);
        }
      }
    };

    checkBackendStatus();
    const interval = setInterval(checkBackendStatus, 15000);

    const handleVisibility = () => {
      if (!document.hidden) {
        checkBackendStatus();
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      isSubscribed = false;
      if (abortController) {
        try {
          abortController.abort();
        } catch {}
      }
      window.removeEventListener("amigo_backend_status", handleBackendStatus);
      document.removeEventListener("visibilitychange", handleVisibility);
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
        {/* App Brand Identity & Backend Connection Status */}
        <div className="flex items-center space-x-2">
          <div
            className="w-6 h-6 rounded-lg flex items-center justify-center text-white shadow-sm transition-transform hover:scale-105"
            style={{ background: theme.gradient }}
          >
            <Sparkles className="w-3.5 h-3.5" />
          </div>
          <div className="flex items-center space-x-1.5">
            <span className="text-xs font-semibold tracking-tight">Amigo</span>
            {onOpenBackendSettings ? (
              <button
                type="button"
                onClick={onOpenBackendSettings}
                aria-label={`Backend ${isOnline ? "Online" : "Offline"}. Click to configure endpoint`}
                title={isOnline ? "Backend Connected (Click to configure)" : "Backend Disconnected (Click to configure)"}
                className={`text-[10px] px-2 py-0.5 rounded-full font-mono border flex items-center space-x-1 transition-all duration-200 cursor-pointer ${
                  isOnline
                    ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/25"
                    : "bg-rose-500/15 text-rose-400 border-rose-500/30 hover:bg-rose-500/25"
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${isOnline ? "bg-emerald-400 animate-pulse" : "bg-rose-400 animate-pulse"}`} />
                <span>{isOnline ? "Online" : "Offline"}</span>
                {isBackendCustom && (
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-400" title="Custom Endpoint" />
                )}
              </button>
            ) : (
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded-full font-mono border flex items-center space-x-1 transition-colors duration-200 ${
                  isOnline
                    ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
                    : "bg-rose-500/15 text-rose-400 border-rose-500/30"
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${isOnline ? "bg-emerald-400 animate-pulse" : "bg-rose-400 animate-pulse"}`} />
                <span>{isOnline ? "Online" : "Offline"}</span>
              </span>
            )}
          </div>
        </div>

        <span className="text-xs opacity-20 hidden sm:inline">|</span>

        {/* History Sidebar Toggle Button */}
        <button
          id="history-sidebar-toggle-btn"
          type="button"
          aria-label="Toggle Command History Panel"
          aria-pressed={showHistory}
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
              className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                showHistory ? "bg-white/25 text-white" : isDark ? "bg-white/10 text-slate-300" : "bg-black/10 text-slate-700"
              }`}
            >
              {historyCount}
            </span>
          )}
        </button>
      </div>

      {/* Right Controls: Endpoint Config & Settings Button */}
      <div className="flex items-center space-x-2">
        {onOpenBackendSettings && (
          <button
            id="backend-settings-modal-btn"
            type="button"
            aria-label="Configure AI Endpoint and Backend"
            onClick={() => {
              sfx.playClick();
              onOpenBackendSettings();
            }}
            className={`relative px-2 py-1 rounded-lg transition-all duration-200 flex items-center space-x-1 text-xs border ${
              isDark
                ? "bg-white/[0.04] hover:bg-white/[0.08] border-white/[0.08] text-slate-300 hover:text-white"
                : "bg-black/[0.04] hover:bg-black/[0.08] border-black/[0.08] text-slate-700 hover:text-black"
            }`}
            title="Backend Endpoint & Model Config"
          >
            <Server className="w-3.5 h-3.5" style={{ color: theme.accent }} />
            <span className="hidden md:inline">Endpoint</span>
          </button>
        )}

        <button
          id="settings-page-toggle-btn"
          type="button"
          aria-label="Toggle Assistant Settings"
          aria-pressed={showSettings}
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
