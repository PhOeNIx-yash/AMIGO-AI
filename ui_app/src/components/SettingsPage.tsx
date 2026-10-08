import React, { useState, useEffect, useMemo, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Settings,
  Palette,
  Server,
  CheckCircle2,
  AlertCircle,
  Sparkles,
  Sun,
  Moon,
  ArrowLeft,
  RotateCcw,
  Activity,
  Check,
  Sliders,
  Type,
  Sidebar,
  Minimize2,
  Maximize2,
  Clock,
  Orbit,
  Database,
  RefreshCw,
  FileText,
  Layers,
  Brain,
  Volume2,
} from "lucide-react";
import {
  BackendConfig,
  ColorTheme,
  PluginMode,
  VisualizerMode,
  ThinkingOrbStyle,
} from "../types";
import { KineticHeading, TextAnimationStyle } from "./KineticText";
import { COLOR_THEMES, GREETING_PRESETS } from "../data/presets";
import { testBackendConnection, fetchRagStatus, triggerRagReindex, fetchAssistantSettings, RagStatusData } from "../services/assistantApi";
import { VoiceSettingsTab } from "./VoiceSettingsTab";
import { THINKING_ORB_PRESETS } from "./CanvasVisualizer";
import { DeleteButton } from "./HistoryPanel";

interface SettingsPageProps {
  isOpen: boolean;
  onClose: () => void;
  isDark: boolean;
  onToggleTheme: () => void;
  colorTheme: ColorTheme;
  onChangeColorTheme: (theme: ColorTheme) => void;
  visualizerMode: VisualizerMode;
  onChangeVisualizerMode: (mode: VisualizerMode) => void;
  thinkingOrbStyle?: ThinkingOrbStyle;
  onChangeThinkingOrbStyle?: (style: ThinkingOrbStyle) => void;
  textAnimationStyle: TextAnimationStyle;
  onChangeTextAnimationStyle: (style: TextAnimationStyle) => void;
  greetingText: string;
  onChangeGreetingText: (text: string) => void;
  autoCycleGreetings: boolean;
  onToggleAutoCycleGreetings: () => void;
  autoCycleInterval?: number;
  onChangeAutoCycleInterval?: (seconds: number) => void;
  pluginMode: PluginMode;
  onChangePluginMode: (mode: PluginMode) => void;
  soundEnabled: boolean;
  onToggleSound: () => void;
  backendConfig: BackendConfig;
  onSaveBackendConfig: (config: BackendConfig) => void;
  historyCount: number;
  onClearHistory: () => void;
  onResetAssistant: () => void;
}


type TabType = "appearance" | "voice" | "backend" | "data";

export const SettingsPage: React.FC<SettingsPageProps> = ({
  isOpen,
  onClose,
  isDark,
  onToggleTheme,
  colorTheme,
  onChangeColorTheme,
  visualizerMode,
  onChangeVisualizerMode,
  thinkingOrbStyle = "globe",
  onChangeThinkingOrbStyle,
  textAnimationStyle,
  onChangeTextAnimationStyle,
  greetingText,
  onChangeGreetingText,
  autoCycleGreetings,
  onToggleAutoCycleGreetings,
  autoCycleInterval = 10,
  onChangeAutoCycleInterval,
  pluginMode,
  onChangePluginMode,
  soundEnabled,
  onToggleSound,
  backendConfig,
  onSaveBackendConfig,
  historyCount,
  onClearHistory,
  onResetAssistant,
}) => {
  const [activeTab, setActiveTab] = useState<TabType>("appearance");
  const theme = useMemo(() => COLOR_THEMES[colorTheme] || COLOR_THEMES.violet, [colorTheme]);

  // Voice state - managed by VoiceSettingsTab
  const [selectedVoice, setSelectedVoice] = useState<string>(() => {
    try {
      const v = localStorage.getItem("amigo_selected_voice");
      return v && v !== "nicole" ? v : "nova";
    } catch {
      return "nova";
    }
  });
  const [voiceFilter, setVoiceFilter] = useState<"all" | "female" | "male">("all");
  const [playingVoiceId, setPlayingVoiceId] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    fetchAssistantSettings(ac.signal)
      .then((data) => {
        let v =
          data?.user_profile?.preferences?.voice ||
          data?.ui_settings?.voice;
        if (!v) {
          try {
            v = localStorage.getItem("amigo_selected_voice");
          } catch (_) {}
        }
        if (v === "nicole") v = "nova";
        if (v) {
          setSelectedVoice(v);
          try {
            localStorage.setItem("amigo_selected_voice", v);
          } catch (_) {}
        }
      })
      .catch((err) => {
        if (err?.name === "AbortError") return;
        try {
          const saved = localStorage.getItem("amigo_selected_voice");
          if (saved) setSelectedVoice(saved === "nicole" ? "nova" : saved);
        } catch (_) {}
      });

    return () => {
      ac.abort();
    };
  }, []);

  // Form states
  const [endpointUrl, setEndpointUrl] = useState(backendConfig.endpointUrl || "/api/assistant/process");
  const [actionWebhookUrl, setActionWebhookUrl] = useState(backendConfig.actionWebhookUrl || "");
  const [apiKey, setApiKey] = useState(backendConfig.apiKey || "");
  const [transcriptionEngine, setTranscriptionEngine] = useState<"amigo-speech" | "whisper" | "web-speech" | "custom">(
    backendConfig.transcriptionEngine || "amigo-speech"
  );
  const [autoSpeech, setAutoSpeech] = useState(backendConfig.autoSpeech !== false);
  const [thinkingEnabled, setThinkingEnabled] = useState(backendConfig.thinkingEnabled === true);

  // Diagnostics
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success?: boolean;
    latencyMs?: number;
    message?: string;
  } | null>(null);

  const [savedBanner, setSavedBanner] = useState(false);
  const savedTimerRef = useRef<any>(null);

  useEffect(() => {
    return () => {
      if (savedTimerRef.current) {
        clearTimeout(savedTimerRef.current);
        savedTimerRef.current = null;
      }
    };
  }, []);

  // Compute dirty state to prevent accidental discards or sync overwrites
  const isDirty = useMemo(() => {
    return (
      endpointUrl.trim() !== (backendConfig.endpointUrl || "/api/assistant/process").trim() ||
      actionWebhookUrl.trim() !== (backendConfig.actionWebhookUrl || "").trim() ||
      apiKey.trim() !== (backendConfig.apiKey || "").trim() ||
      transcriptionEngine !== (backendConfig.transcriptionEngine || "amigo-speech") ||
      autoSpeech !== (backendConfig.autoSpeech !== false) ||
      thinkingEnabled !== (backendConfig.thinkingEnabled === true)
    );
  }, [endpointUrl, actionWebhookUrl, apiKey, transcriptionEngine, autoSpeech, thinkingEnabled, backendConfig]);

  // Sync state if config changes from outside, only if user is not actively editing
  const prevIsOpenRef = useRef(isOpen);
  useEffect(() => {
    // When modal opens fresh, always sync to latest backendConfig
    if (isOpen && !prevIsOpenRef.current) {
      setEndpointUrl(backendConfig.endpointUrl || "/api/assistant/process");
      setActionWebhookUrl(backendConfig.actionWebhookUrl || "");
      setApiKey(backendConfig.apiKey || "");
      setTranscriptionEngine(backendConfig.transcriptionEngine || "amigo-speech");
      setAutoSpeech(backendConfig.autoSpeech !== false);
      setThinkingEnabled(backendConfig.thinkingEnabled === true);
    } else if (!isDirty) {
      setEndpointUrl(backendConfig.endpointUrl || "/api/assistant/process");
      setActionWebhookUrl(backendConfig.actionWebhookUrl || "");
      setApiKey(backendConfig.apiKey || "");
      setTranscriptionEngine(backendConfig.transcriptionEngine || "amigo-speech");
      setAutoSpeech(backendConfig.autoSpeech !== false);
      setThinkingEnabled(backendConfig.thinkingEnabled === true);
    }
    prevIsOpenRef.current = isOpen;
  }, [isOpen, backendConfig, isDirty]);

  // RAG / Knowledge Base states
  const [ragStatus, setRagStatus] = useState<RagStatusData | null>(null);
  const [isReindexing, setIsReindexing] = useState(false);
  const [reindexMsg, setReindexMsg] = useState<{ success: boolean; text: string } | null>(null);
  const [indexingProgress, setIndexingProgress] = useState<{
    is_indexing: boolean;
    percent: number;
    current_file: string;
    files_processed: number;
    files_total: number;
    files_indexed: number;
    files_skipped: number;
    status_message?: string;
    completed?: boolean;
  } | null>(null);
  const dismissTimerRef = useRef<any>(null);
  const inFlightRagRef = useRef(false);

  const loadRagStatus = async () => {
    if (inFlightRagRef.current) return;
    inFlightRagRef.current = true;
    try {
      const stats = await fetchRagStatus();
      setRagStatus(stats);
      if (stats.indexer?.is_indexing) {
        setIsReindexing(true);
      } else if (!stats.is_indexing && !stats.indexer?.is_indexing && !indexingProgress?.is_indexing) {
        setIsReindexing(false);
      }
    } catch (e) {
    } finally {
      inFlightRagRef.current = false;
    }
  };

  // Real-time SSE progress listener for background indexing
  useEffect(() => {
    const handleSSE = (e: any) => {
      const detail = e.detail;
      if (detail?.type === "rag_indexing_progress") {
        const isIdx = Boolean(detail.is_indexing);
        setIndexingProgress({
          is_indexing: isIdx,
          percent: detail.progress_percent ?? 0,
          current_file: detail.current_file || "",
          files_processed: detail.files_processed || 0,
          files_total: detail.files_total || 0,
          files_indexed: detail.files_indexed || 0,
          files_skipped: detail.files_skipped || 0,
          status_message: detail.status_message,
          completed: !isIdx,
        });

        if (isIdx) {
          setIsReindexing(true);
          if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
        } else {
          setIsReindexing(false);
          loadRagStatus();
          // Keep completion status visible for 6s so user sees the 100% finished state
          if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
          dismissTimerRef.current = setTimeout(() => {
            setIndexingProgress(null);
          }, 6000);
        }
      }
    };
    window.addEventListener("amigo_sse", handleSSE);
    return () => {
      window.removeEventListener("amigo_sse", handleSSE);
      if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
    };
  }, []);

  // Stable polling effect with in-flight guard and busy-state awareness
  const isBusyRef = useRef(false);
  isBusyRef.current = Boolean(isReindexing || ragStatus?.is_indexing || ragStatus?.indexer?.is_indexing || indexingProgress?.is_indexing);

  useEffect(() => {
    if (!isOpen || activeTab !== "data") return;
    loadRagStatus();

    // Poll every 3s if actively indexing, or 6s when monitoring idle status
    const pollInterval = isBusyRef.current ? 3000 : 6000;
    const interval = setInterval(() => {
      loadRagStatus();
    }, pollInterval);
    return () => clearInterval(interval);
  }, [isOpen, activeTab]);

  const handleTriggerReindex = async () => {
    setIsReindexing(true);
    setReindexMsg(null);
    setIndexingProgress({
      is_indexing: true,
      percent: 0,
      current_file: "Scanning documents...",
      files_processed: 0,
      files_total: ragStatus?.documents || 0,
      files_indexed: 0,
      files_skipped: 0,
      status_message: "Starting full re-index...",
      completed: false,
    });
    try {
      const res = await triggerRagReindex(true);
      setReindexMsg({ success: true, text: res.message || "Re-indexing started in background" });
      await loadRagStatus();
    } catch (e: any) {
      setReindexMsg({ success: false, text: e.message || "Failed to start re-indexing" });
      setIsReindexing(false);
      setIndexingProgress(null);
    }
  };

  const handleSave = () => {
    const updated: BackendConfig = {
      ...backendConfig,
      endpointUrl: endpointUrl.trim(),
      actionWebhookUrl: actionWebhookUrl.trim() || undefined,
      apiKey: apiKey.trim() || undefined,
      protocol: "rest",
      autoSpeech,
      transcriptionEngine,
      thinkingEnabled,
    };
    onSaveBackendConfig(updated);
    if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
    setSavedBanner(true);
    savedTimerRef.current = setTimeout(() => setSavedBanner(false), 2000);
  };

  const handleCloseWithCheck = () => {
    if (isDirty) {
      if (window.confirm("You have unsaved changes in AI & Endpoint settings. Would you like to save them before exiting?")) {
        handleSave();
      }
    }
    onClose();
  };

  // Keyboard navigation: Escape key closes with unsaved check
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        handleCloseWithCheck();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, isDirty, endpointUrl, actionWebhookUrl, apiKey, transcriptionEngine, autoSpeech, thinkingEnabled, backendConfig]);

  const handleTestBackend = async () => {
    setTesting(true);
    setTestResult(null);

    const testConf: BackendConfig = {
      ...backendConfig,
      endpointUrl,
      actionWebhookUrl,
      apiKey,
      protocol: "rest",
      autoSpeech,
      transcriptionEngine,
      thinkingEnabled,
    };

    const result = await testBackendConnection(testConf);
    setTestResult(result);
    setTesting(false);
  };

  const handleResetToDefaults = () => {
    if (!window.confirm("Are you sure you want to reset all assistant settings to default values?")) {
      return;
    }
    onChangeColorTheme?.("beams");
    onChangeVisualizerMode("orb");
    onChangeThinkingOrbStyle?.("globe");
    onChangeTextAnimationStyle("silk_blur");
    onChangePluginMode("fullscreen");
    setTranscriptionEngine("amigo-speech");
    setAutoSpeech(true);
    setThinkingEnabled(false);
    setEndpointUrl("/api/assistant/process");
    setActionWebhookUrl("");
    setApiKey("");

    const defaultBackend: BackendConfig = {
      ...backendConfig,
      endpointUrl: "/api/assistant/process",
      actionWebhookUrl: "",
      apiKey: "",
      protocol: "rest",
      autoSpeech: true,
      transcriptionEngine: "amigo-speech",
      thinkingEnabled: false,
    };
    onSaveBackendConfig(defaultBackend);
    onResetAssistant();

    if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
    setSavedBanner(true);
    savedTimerRef.current = setTimeout(() => setSavedBanner(false), 2000);
  };

  const tabs = [
    { id: "appearance", label: "Appearance", icon: Palette },
    { id: "voice", label: "Voice", icon: Volume2 },
    { id: "backend", label: "AI & Endpoint", icon: Server },
    { id: "data", label: "Knowledge & Data", icon: Database },
  ] as const;

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 12 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
          className={`fixed inset-0 z-[100] h-[var(--visual-viewport-height,100dvh)] flex flex-col shadow-2xl overflow-hidden ${
            isDark ? "text-white" : "text-slate-900"
          }`}
          style={{
            willChange: "transform, opacity",
            transform: "translate3d(0, 0, 0)",
            background: isDark
              ? `radial-gradient(ellipse 120% 70% at 50% 0%, ${theme.primary}15 0%, #0b0a17 70%)`
              : `radial-gradient(ellipse 120% 70% at 50% 0%, ${theme.primary}08 0%, #f8f9fc 70%)`,
          }}
        >
          {/* Header - Fixed layout with grid to prevent overlap */}
          <div
            className={`relative px-4 sm:px-6 pt-[max(4rem,calc(env(safe-area-inset-top)+3.5rem))] pb-3 border-b flex-shrink-0 flex items-center justify-between ${
              isDark ? "bg-black/50" : "bg-white/95"
            }`}
            style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
          >
            {/* Left section - Back button + Title */}
            <div className="flex items-center space-x-3 min-w-0">
              <button
                id="settings-back-btn"
                onClick={handleCloseWithCheck}
                className={`p-2 rounded-xl border transition-colors flex items-center justify-center flex-shrink-0 ${
                  isDark
                    ? "bg-white/5 border-white/10 hover:bg-white/10 text-slate-200"
                    : "bg-black/5 border-black/10 hover:bg-black/10 text-slate-800"
                }`}
                title="Done & Close Settings"
              >
                <ArrowLeft className="w-4 h-4" />
              </button>
              <div className="min-w-0">
                <div className="flex items-center space-x-2">
                  <Settings className="w-4 h-4 flex-shrink-0" style={{ color: theme.accent }} />
                  <h2 className="text-base sm:text-lg font-semibold tracking-tight truncate">Settings</h2>
                </div>
                <p className="text-xs opacity-60 hidden sm:block truncate">Personalize themes, visualizers, and API settings</p>
              </div>
            </div>

            {/* Right section - Save button (flex aligned to center with left content) */}
            <div className="flex items-center space-x-2 flex-shrink-0 z-10 ml-4">
              {savedBanner && (
                <span className="text-xs font-semibold text-emerald-400 flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/20 animate-fade-in flex-shrink-0">
                  <Check className="w-3.5 h-3.5" />
                  <span>Saved</span>
                </span>
              )}

              <button
                id="settings-save-btn"
                onClick={handleSave}
                className="px-4 py-1.5 rounded-xl text-xs font-semibold text-white shadow-sm transition-transform active:scale-95 flex-shrink-0"
                style={{
                  background: theme.gradient,
                }}
              >
                Save
              </button>
            </div>
          </div>

          {/* Main Layout */}
          <div className="flex-1 min-h-0 flex flex-col md:flex-row overflow-hidden">
            {/* Horizontal Tabs Bar on Mobile / Sidebar on Desktop */}
            <div
              className={`w-full md:w-52 flex-shrink-0 p-2 sm:p-3 border-b md:border-b-0 md:border-r flex md:flex-col space-x-1.5 md:space-x-0 md:space-y-1 overflow-x-auto no-scrollbar whitespace-nowrap ${
                isDark ? "bg-black/30" : "bg-slate-50"
              }`}
              style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
            >
              {tabs.map((tab) => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className={`flex items-center space-x-2 px-3.5 py-2 md:py-2.5 rounded-xl text-xs font-medium transition-all flex-shrink-0 border-b-2 md:border-b-0 md:border-l-[3px] ${
                      isActive
                        ? "font-semibold shadow-sm"
                        : "border-transparent"
                    } ${
                      isActive
                        ? isDark
                          ? "text-white"
                          : "text-slate-900"
                        : isDark
                        ? "text-slate-400 hover:text-white hover:bg-white/5"
                        : "text-slate-600 hover:text-slate-900 hover:bg-black/5"
                    }`}
                    style={
                      isActive
                        ? {
                            borderColor: theme.primary,
                            backgroundColor: isDark ? `${theme.primary}25` : `${theme.primary}14`,
                            color: isDark ? (theme.accent || theme.primary) : theme.primary,
                          }
                        : {}
                    }
                  >
                    <Icon className="w-4 h-4 flex-shrink-0" />
                    <span className="whitespace-nowrap">{tab.label}</span>
                  </button>
                );
              })}
            </div>

            {/* Content Area with smooth tab transition physics and mobile safe area bottom */}
            <div className="flex-1 min-h-0 overflow-y-auto p-4 sm:p-6 pb-[max(3rem,calc(env(safe-area-inset-bottom)+2.5rem))] smooth-scroll-container">
              <AnimatePresence mode="wait">
                <motion.div
                  key={activeTab}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                  className="max-w-xl mx-auto space-y-6"
                >
            {/* 1. APPEARANCE TAB */}
            {activeTab === "appearance" && (
              <>
                {/* Dark / Light Toggle */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03]" : "bg-white"
                  }`}
                  style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                >
                  <div className="text-xs font-semibold uppercase tracking-wider opacity-60 mb-3">Theme Mode</div>
                  <div className="grid grid-cols-2 gap-3">
                    <button
                      onClick={() => {
                        if (!isDark) return;
                        onToggleTheme();
                      }}
                      className={`p-3 rounded-xl border text-left flex items-center space-x-3 transition-all ${
                        !isDark
                          ? "bg-white text-slate-900 shadow-sm"
                          : "border-white/10 bg-white/5 opacity-70 hover:opacity-100 text-white"
                      }`}
                      style={
                        !isDark
                          ? {
                              borderColor: `${theme.primary}80`,
                              boxShadow: `0 0 0 2px ${theme.primary}30`,
                            }
                          : {}
                      }
                    >
                      <Sun className="w-5 h-5 text-amber-500 flex-shrink-0" />
                      <div>
                        <div className="text-xs font-semibold">Light</div>
                        <div className="text-[10px] opacity-60">Clean high-contrast</div>
                      </div>
                    </button>

                    <button
                      onClick={() => {
                        if (isDark) return;
                        onToggleTheme();
                      }}
                      className={`p-3 rounded-xl border text-left flex items-center space-x-3 transition-all ${
                        isDark
                          ? "bg-black/60 text-white shadow-sm"
                          : "border-black/10 bg-black/5 opacity-70 hover:opacity-100 text-slate-900"
                      }`}
                      style={
                        isDark
                          ? {
                              borderColor: `${theme.primary}80`,
                              boxShadow: `0 0 0 2px ${theme.primary}30`,
                            }
                          : {}
                      }
                    >
                      <Moon className="w-5 h-5 flex-shrink-0" style={{ color: theme.accent || theme.primary }} />
                      <div>
                        <div className="text-xs font-semibold">Dark</div>
                        <div className="text-[10px] opacity-60">Obsidian fluent</div>
                      </div>
                    </button>
                  </div>
                </div>


                {/* Thinking Orb Animation Selector */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03]" : "bg-white"
                  }`}
                  style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                >
                  <div className="flex items-start gap-3 mb-3">
                    <div
                      className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl border"
                      style={{ color: theme.accent, borderColor: `${theme.accent}55`, backgroundColor: `${theme.accent}12` }}
                    >
                      <Orbit className="h-4 w-4" />
                    </div>
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-wider opacity-60">Thinking Orb Animation</div>
                      <p className="mt-1 text-[11px] leading-relaxed opacity-60">
                        Choose the 3D particle motion Amigo displays while processing commands and reasoning.
                      </p>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                    {THINKING_ORB_PRESETS.map((preset) => {
                      const isSelected = thinkingOrbStyle === preset.id;
                      return (
                        <button
                          key={preset.id}
                          type="button"
                          onClick={() => onChangeThinkingOrbStyle?.(preset.id)}
                          className={`p-2.5 rounded-xl border flex flex-col items-start text-left transition-all relative overflow-hidden ${
                            isSelected
                              ? "shadow-sm font-medium"
                              : isDark
                              ? "border-white/10 bg-white/[0.02] hover:bg-white/5"
                              : "border-black/10 bg-white hover:bg-slate-50"
                          }`}
                          style={
                            isSelected
                              ? {
                                  borderColor: theme.primary,
                                  boxShadow: `0 0 0 1.5px ${theme.primary}40`,
                                  backgroundColor: `${theme.primary}12`,
                                }
                              : {}
                          }
                        >
                          <div className="flex items-center justify-between w-full">
                            <span className="text-xs font-semibold text-slate-900 dark:text-white flex items-center gap-1.5">
                              <span
                                className="w-2 h-2 rounded-full inline-block"
                                style={{ backgroundColor: isSelected ? theme.primary : "rgba(150,150,150,0.5)" }}
                              />
                              {preset.name}
                            </span>
                            {isSelected && (
                              <span
                                className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                                style={{ backgroundColor: `${theme.primary}25`, color: theme.primary }}
                              >
                                Active
                              </span>
                            )}
                          </div>
                          <span className="text-[10px] opacity-60 mt-1 line-clamp-1">
                            {preset.desc}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>


                {/* Greeting Headline & Prompt Options */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03]" : "bg-white"
                  } space-y-3`}
                  style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-wider opacity-60">Headline Greeting Text</div>
                      <div className="text-[11px] opacity-60">Choose what Amigo greets you with when idle</div>
                    </div>
                    {/* Auto-cycle toggle */}
                    <button
                      type="button"
                      role="switch"
                      aria-checked={autoCycleGreetings}
                      onClick={onToggleAutoCycleGreetings}
                      className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold border transition-all flex items-center space-x-1.5 ${
                        autoCycleGreetings
                          ? ""
                          : isDark
                          ? "border-white/10 bg-white/5 text-slate-400 hover:text-slate-200"
                          : "border-black/10 bg-black/5 text-slate-600 hover:text-slate-900"
                      }`}
                      style={
                        autoCycleGreetings
                          ? {
                              borderColor: `${theme.primary}80`,
                              backgroundColor: `${theme.primary}15`,
                              color: theme.accent || theme.primary,
                            }
                          : {}
                      }
                      title="Automatically cycle through greeting phrases when idle"
                    >
                      <RotateCcw className={`w-3 h-3 ${autoCycleGreetings ? "animate-spin" : ""}`} />
                      <span>{autoCycleGreetings ? "Auto-Cycling: ON" : "Auto-Cycle: OFF"}</span>
                    </button>
                  </div>

                  {/* Auto-cycle rotation speed slider & presets */}
                  {autoCycleGreetings && onChangeAutoCycleInterval && (
                    <div
                      className={`p-3 rounded-xl border space-y-2.5 ${
                        isDark ? "bg-white/[0.02]" : "bg-black/[0.02]"
                      }`}
                      style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                    >
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-medium opacity-80 flex items-center space-x-1.5">
                          <Clock className="w-3.5 h-3.5" style={{ color: theme.accent }} />
                          <span>Text Change Timing</span>
                        </span>
                        <span
                          className="font-mono text-[11px] px-2 py-0.5 rounded-md font-semibold"
                          style={{ backgroundColor: `${theme.primary}25`, color: theme.accent }}
                        >
                          {autoCycleInterval}s per phrase
                        </span>
                      </div>

                      <div className="flex items-center space-x-3">
                        <span className="text-[10px] opacity-40 font-mono">3s</span>
                        <input
                          type="range"
                          min={3}
                          max={30}
                          step={1}
                          value={autoCycleInterval}
                          onChange={(e) => onChangeAutoCycleInterval(parseInt(e.target.value, 10))}
                          className="w-full h-1.5 rounded-lg appearance-none cursor-pointer"
                          style={{ accentColor: theme.primary }}
                        />
                        <span className="text-[10px] opacity-40 font-mono">30s</span>
                      </div>

                      <div className="flex items-center space-x-1.5 pt-0.5">
                        <span className="text-[10px] opacity-50 mr-1">Quick:</span>
                        {[5, 8, 10, 15, 20, 30].map((sec) => (
                          <button
                            key={sec}
                            type="button"
                            onClick={() => onChangeAutoCycleInterval(sec)}
                            className={`px-2 py-0.5 rounded-md text-[10px] font-semibold border transition-all ${
                              autoCycleInterval === sec
                                ? "text-white shadow-sm"
                                : isDark
                                ? "bg-white/5 border-white/10 hover:bg-white/10 text-slate-300"
                                : "bg-black/5 border-black/10 hover:bg-black/10 text-slate-700"
                            }`}
                            style={autoCycleInterval === sec ? { backgroundColor: theme.primary, borderColor: theme.primary } : {}}
                          >
                            {sec}s
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Custom greeting input */}
                  <div>
                    <input
                      type="text"
                      value={greetingText}
                      onChange={(e) => onChangeGreetingText(e.target.value)}
                      placeholder="Enter custom greeting phrase..."
                      className={`w-full py-2 px-3 rounded-xl border text-xs outline-none transition-all ${
                        isDark
                          ? "bg-black/40 border-white/10 text-white"
                          : "bg-slate-50 border-black/10 text-slate-900"
                      }`}
                      style={{ borderColor: isDark ? `${theme.primary}22` : `${theme.primary}18` }}
                    />
                  </div>

                  {/* Greeting Presets */}
                  <div className="space-y-1.5 pt-1">
                    <div className="text-[11px] font-medium opacity-50">Quick Presets:</div>
                    <div className="flex flex-wrap gap-1.5">
                      {GREETING_PRESETS.map((preset) => {
                        const isSelected = greetingText.trim() === preset.text.trim();
                        return (
                          <button
                            key={preset.id}
                            onClick={() => onChangeGreetingText(preset.text)}
                            className={`px-2.5 py-1 rounded-lg text-xs transition-all text-left flex items-center space-x-1.5 border ${
                              isSelected
                                ? "font-medium"
                                : isDark
                                ? "border-white/[0.08] bg-white/[0.02] text-slate-300 hover:bg-white/5 hover:text-white"
                                : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                            }`}
                            style={
                              isSelected
                                ? {
                                    borderColor: `${theme.primary}80`,
                                    backgroundColor: `${theme.primary}15`,
                                    color: isDark ? (theme.accent || theme.primary) : theme.primary,
                                  }
                                : {}
                            }
                          >
                            <span className="truncate max-w-[200px]">{preset.text}</span>
                            {isSelected && (
                              <Check
                                className="w-3 h-3 flex-shrink-0"
                                style={{ color: theme.accent || theme.primary }}
                              />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>

                {/* Text Animation Style (AmazingUI) */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03]" : "bg-white"
                  }`}
                  style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                >
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-wider opacity-60">Typography & Animation Style</div>
                      <div className="text-[11px] opacity-60">Curated, fluid kinetic animations for headings and voice responses</div>
                    </div>
                    <span
                      className="text-[10px] px-2 py-0.5 rounded-full font-medium border"
                      style={{
                        backgroundColor: `${theme.primary}15`,
                        color: theme.accent || theme.primary,
                        borderColor: `${theme.primary}30`,
                      }}
                    >
                      4 Curated Styles
                    </span>
                  </div>

                  {/* Live Interactive Kinetic Preview Box */}
                  <div
                    className={`mb-3.5 p-4 rounded-xl border flex flex-col items-center justify-center min-h-[76px] transition-all overflow-hidden ${
                      isDark ? "bg-black/40" : "bg-slate-50"
                    }`}
                    style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                  >
                    <div className="text-[9px] font-mono uppercase tracking-widest opacity-40 mb-1.5">
                      Live Kinetic Physics Preview
                    </div>
                    <KineticHeading
                      text="Hello Amigo, what is the plan for today?"
                      isDark={isDark}
                      colorTheme={colorTheme}
                      animationStyle={textAnimationStyle}
                      className="text-base sm:text-lg font-medium tracking-tight"
                    />
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                    {[
                      {
                        id: "silk_blur",
                        label: "Silk Emerge",
                        badge: "Apple Keynote",
                        desc: "Cinematic Gaussian blur dissipation & gentle organic drift",
                      },
                      {
                        id: "fluid_glide",
                        label: "Liquid Glide",
                        badge: "Organic Spring",
                        desc: "Critically-damped upward glide with zero cartoon bounce",
                      },
                      {
                        id: "ambient_shimmer",
                        label: "Specular Sheen",
                        badge: "Linear Style",
                        desc: "Refined metallic light sheen sweep across typography",
                      },
                      {
                        id: "calm_breathe",
                        label: "Serene Float",
                        badge: "Tranquil",
                        desc: "Subtle low-amplitude anti-gravity breath for calm focus",
                      },
                    ].map((styleItem) => {
                      const isSelected = textAnimationStyle === styleItem.id;
                      return (
                        <button
                          key={styleItem.id}
                          onClick={() => onChangeTextAnimationStyle(styleItem.id as TextAnimationStyle)}
                          className={`p-3 rounded-xl border text-left transition-all relative overflow-hidden ${
                            isSelected
                              ? "shadow-sm"
                              : isDark
                              ? "border-white/10 bg-white/[0.02] hover:bg-white/5"
                              : "border-black/10 bg-white hover:bg-slate-50"
                          }`}
                          style={
                            isSelected
                              ? {
                                  borderColor: `${theme.primary}80`,
                                  boxShadow: `0 0 0 1px ${theme.primary}30`,
                                  backgroundColor: `${theme.primary}12`,
                                }
                              : {}
                          }
                        >
                          <div className="flex items-center justify-between mb-1">
                            <div className="flex items-center space-x-2 text-xs font-semibold">
                              <Type className="w-3.5 h-3.5" style={{ color: theme.accent }} />
                              <span>{styleItem.label}</span>
                              <span
                                className="text-[9px] px-1.5 py-0.5 rounded font-mono"
                                style={
                                  isSelected
                                    ? {
                                        backgroundColor: `${theme.primary}25`,
                                        color: isDark ? (theme.accent || theme.primary) : theme.primary,
                                      }
                                    : {
                                        backgroundColor: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.08)",
                                        color: isDark ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.6)",
                                      }
                                }
                              >
                                {styleItem.badge}
                              </span>
                            </div>
                            {isSelected && (
                              <Check
                                className="w-4 h-4 flex-shrink-0"
                                style={{ color: theme.accent || theme.primary }}
                              />
                            )}
                          </div>
                          <div className="text-[11px] leading-snug opacity-60">{styleItem.desc}</div>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Plugin Docking / Window Layout */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03]" : "bg-white"
                  }`}
                  style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                >
                  <div className="text-xs font-semibold uppercase tracking-wider opacity-60 mb-3">Plugin Window Layout</div>
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { id: "floating", label: "Floating Widget", icon: Sliders, desc: "Draggable centered modal" },
                      { id: "docked_right", label: "Dock Right Sidebar", icon: Sidebar, desc: "Full-height side panel" },
                      { id: "docked_bottom", label: "Dock Bottom Bar", icon: Minimize2, desc: "Lower bottom bar dock" },
                      { id: "fullscreen", label: "Full Window", icon: Maximize2, desc: "Expanded fullscreen workspace" },
                    ].map((modeItem) => {
                      const Icon = modeItem.icon;
                      const isSelected = pluginMode === modeItem.id;
                      return (
                        <button
                          key={modeItem.id}
                          onClick={() => onChangePluginMode(modeItem.id as PluginMode)}
                          className={`p-2.5 rounded-xl border text-left transition-all ${
                            isSelected
                              ? "shadow-sm"
                              : isDark
                              ? "border-white/10 bg-white/[0.02] hover:bg-white/5"
                              : "border-black/10 bg-white hover:bg-slate-50"
                          }`}
                          style={
                            isSelected
                              ? {
                                  borderColor: `${theme.primary}80`,
                                  boxShadow: `0 0 0 1px ${theme.primary}30`,
                                  backgroundColor: `${theme.primary}12`,
                                }
                              : {}
                          }
                        >
                          <div className="flex items-center space-x-1.5 mb-0.5">
                            <Icon className="w-3.5 h-3.5" style={{ color: theme.accent }} />
                            <div className="text-xs font-semibold">{modeItem.label}</div>
                          </div>
                          <div className="text-[10px] opacity-60">{modeItem.desc}</div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </>
            )}

            {/* VOICE SETTINGS TAB */}
            {activeTab === "voice" && (
              <VoiceSettingsTab
                isDark={isDark}
                theme={theme}
                selectedVoice={selectedVoice}
                setSelectedVoice={setSelectedVoice}
                playingVoiceId={playingVoiceId}
                setPlayingVoiceId={setPlayingVoiceId}
                voiceFilter={voiceFilter}
                setVoiceFilter={setVoiceFilter}
              />
            )}

            {/* 2. BACKEND & AI TAB */}
            {activeTab === "backend" && (
              <div
                className={`p-4 rounded-2xl border ${
                  isDark ? "bg-white/[0.03]" : "bg-white"
                } space-y-4`}
                style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
              >
                <div className="text-xs font-semibold uppercase tracking-wider opacity-60">Assistant Gateway</div>

                <div>
                  <label className="text-xs font-medium block mb-1">API Endpoint URL</label>
                  <input
                    type="text"
                    value={endpointUrl}
                    onChange={(e) => setEndpointUrl(e.target.value)}
                    placeholder="/api/assistant/process"
                    className={`w-full p-2.5 rounded-xl border text-xs font-mono outline-none ${
                      isDark ? "bg-black/40 border-white/10 text-white" : "bg-slate-50 border-black/10 text-slate-900"
                    }`}
                    style={{ borderColor: isDark ? `${theme.primary}22` : `${theme.primary}18` }}
                  />
                  <p className="text-[10px] opacity-50 mt-1">Default local route handles Gemini 2.5 Flash processing</p>
                </div>

                <div>
                  <label className="text-xs font-medium block mb-1">Action Webhook URL (Optional)</label>
                  <input
                    type="text"
                    value={actionWebhookUrl}
                    onChange={(e) => setActionWebhookUrl(e.target.value)}
                    placeholder="https://webhook.site/your-id"
                    className={`w-full p-2.5 rounded-xl border text-xs font-mono outline-none ${
                      isDark ? "bg-black/40 border-white/10 text-white" : "bg-slate-50 border-black/10 text-slate-900"
                    }`}
                    style={{ borderColor: isDark ? `${theme.primary}22` : `${theme.primary}18` }}
                  />
                </div>

                <div>
                  <label className="text-xs font-medium block mb-1">Custom API Key (Optional)</label>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="Bearer token or API key"
                    className={`w-full p-2.5 rounded-xl border text-xs font-mono outline-none ${
                      isDark ? "bg-black/40 border-white/10 text-white" : "bg-slate-50 border-black/10 text-slate-900"
                    }`}
                    style={{ borderColor: isDark ? `${theme.primary}22` : `${theme.primary}18` }}
                  />
                </div>

                {/* Speech Recognition & Voice Output Engine */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                  <div>
                    <label htmlFor="settings-transcription-engine" className="block text-xs font-semibold uppercase tracking-wider mb-1.5 opacity-80">
                      Voice Transcription STT
                    </label>
                    <select
                      id="settings-transcription-engine"
                      value={transcriptionEngine}
                      onChange={(e) => {
                        setTranscriptionEngine(e.target.value as any);
                        if (testResult) setTestResult(null);
                      }}
                      className={`w-full p-2 rounded-xl border text-xs font-medium focus:outline-none focus:ring-2 focus:ring-indigo-500/50 ${
                        isDark ? "bg-[#181829] border-white/10 text-white" : "bg-white border-black/10 text-slate-900"
                      }`}
                    >
                      <option value="amigo-speech" className={isDark ? "bg-[#181829] text-white" : "bg-white text-slate-900"}>
                        Amigo Local Audio STT
                      </option>
                      <option value="web-speech" className={isDark ? "bg-[#181829] text-white" : "bg-white text-slate-900"}>
                        Browser Web Speech API
                      </option>
                    </select>
                  </div>

                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider mb-1.5 opacity-80">
                      Voice Output TTS
                    </label>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={autoSpeech}
                      aria-label="Toggle auto-speak responses"
                      onClick={() => {
                        setAutoSpeech(!autoSpeech);
                        if (testResult) setTestResult(null);
                      }}
                      className={`w-full p-2 rounded-xl border text-xs font-medium flex items-center justify-between transition-all ${
                        autoSpeech
                          ? isDark
                            ? "bg-indigo-600/20 border-indigo-500/40 text-indigo-300"
                            : "bg-indigo-50 border-indigo-300 text-indigo-700"
                          : isDark
                          ? "bg-white/5 border-white/10 opacity-60"
                          : "bg-black/5 border-black/10 opacity-60"
                      }`}
                    >
                      <span>Auto-speak responses</span>
                      <span className="font-semibold">{autoSpeech ? "ON" : "OFF"}</span>
                    </button>
                  </div>
                </div>

                {/* Reasoning / Thinking Mode Toggle */}
                <div
                  className={`p-3.5 rounded-xl border transition-colors ${
                    thinkingEnabled
                      ? isDark
                        ? "bg-purple-950/20 border-purple-500/40"
                        : "bg-purple-50 border-purple-300"
                      : isDark
                      ? "bg-white/[0.02] border-white/10"
                      : "bg-slate-50 border-black/10"
                  }`}
                  style={thinkingEnabled ? { borderColor: `${theme.primary}50` } : {}}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-2.5">
                      <div
                        className={`w-7 h-7 rounded-lg flex items-center justify-center ${
                          thinkingEnabled
                            ? "text-white shadow-sm"
                            : isDark
                            ? "bg-white/10 text-slate-400"
                            : "bg-slate-200 text-slate-600"
                        }`}
                        style={thinkingEnabled ? { backgroundColor: theme.primary } : {}}
                      >
                        <Brain className="w-4 h-4" />
                      </div>
                      <div>
                        <div className="text-xs font-semibold flex items-center space-x-1.5">
                          <span>Deep Reasoning Mode</span>
                        </div>
                        <div className="text-[11px] opacity-60">
                          {thinkingEnabled
                            ? "Enabled — Model performs step-by-step reasoning before answering"
                            : "Disabled — Direct, fast conversational responses"}
                        </div>
                      </div>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={thinkingEnabled}
                      aria-label="Toggle deep reasoning mode"
                      onClick={() => setThinkingEnabled(!thinkingEnabled)}
                      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${
                        isDark && !thinkingEnabled ? "bg-white/20" : !thinkingEnabled ? "bg-slate-300" : ""
                      }`}
                      style={thinkingEnabled ? { backgroundColor: theme.primary } : {}}
                    >
                      <span
                        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                          thinkingEnabled ? "translate-x-6" : "translate-x-1"
                        }`}
                      />
                    </button>
                  </div>
                  <p className="text-[10px] opacity-50 mt-2">
                    When enabled, reasoning steps are preserved in chat history and excluded from voice output.
                  </p>
                </div>

                {/* Diagnostics Test Button */}
                <div className="pt-2 flex flex-wrap items-center justify-between gap-2 border-t border-white/5">
                  <button
                    onClick={handleTestBackend}
                    disabled={testing}
                    className="px-3.5 py-1.5 rounded-xl text-xs font-semibold border transition-colors flex items-center space-x-1.5"
                    style={{
                      borderColor: `${theme.primary}40`,
                      backgroundColor: `${theme.primary}15`,
                      color: isDark ? (theme.accent || theme.primary) : theme.primary,
                    }}
                  >
                    <Activity className="w-3.5 h-3.5" />
                    <span>{testing ? "Testing..." : "Test Connection"}</span>
                  </button>

                  {testResult && (
                    <div
                      className={`text-xs font-medium px-2.5 py-1 rounded-lg flex items-center space-x-1.5 ${
                        testResult.success
                          ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                          : "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                      }`}
                    >
                      {testResult.success ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
                      <span>
                        {testResult.message} ({testResult.latencyMs}ms)
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* 4. DATA & RESET TAB */}
            {activeTab === "data" && (
              <div
                className={`p-4 rounded-2xl border ${
                  isDark ? "bg-white/[0.03]" : "bg-white"
                } space-y-4`}
                style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
              >
                <div className="text-xs font-semibold uppercase tracking-wider opacity-60">Knowledge & Data Management</div>

                {/* Knowledge Base & Document Indexing Visual Widget */}
                <div className={`p-3.5 rounded-xl border ${isDark ? "bg-black/30 border-white/5" : "bg-slate-50 border-black/5"} space-y-3`}>
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
                    <div>
                      <div className="text-xs font-semibold flex items-center space-x-1.5">
                        <Database className="w-3.5 h-3.5" style={{ color: theme.accent || theme.primary }} />
                        <span>Knowledge Base & Document Index</span>
                      </div>
                      <div className="text-[11px] opacity-60 mt-0.5">
                        Local offline ChromaDB vector store for fast document search
                      </div>
                    </div>
                    <button
                      onClick={handleTriggerReindex}
                      disabled={isReindexing || Boolean(ragStatus?.indexer?.is_indexing) || Boolean(indexingProgress?.is_indexing)}
                      className="self-start sm:self-auto px-3 py-1.5 rounded-xl text-xs font-semibold border disabled:opacity-50 transition-colors flex items-center space-x-1.5 shadow-sm"
                      style={{
                        borderColor: `${theme.primary}40`,
                        backgroundColor: `${theme.primary}15`,
                        color: isDark ? (theme.accent || theme.primary) : theme.primary,
                      }}
                    >
                      <RefreshCw className={`w-3.5 h-3.5 ${isReindexing || Boolean(ragStatus?.indexer?.is_indexing) || Boolean(indexingProgress?.is_indexing) ? "animate-spin" : ""}`} />
                      <span>{isReindexing || Boolean(ragStatus?.indexer?.is_indexing) || Boolean(indexingProgress?.is_indexing) ? "Indexing..." : "Re-index Files"}</span>
                    </button>
                  </div>

                  {/* Live Progress Bar (when active or recently run) */}
                  {(isReindexing || Boolean(ragStatus?.indexer?.is_indexing) || Boolean(indexingProgress)) && (
                    <div
                      className="p-3 rounded-lg space-y-2 border"
                      style={{
                        backgroundColor: `${theme.primary}10`,
                        borderColor: `${theme.primary}25`,
                      }}
                    >
                      <div className="flex items-center justify-between text-xs">
                        <div
                          className="flex items-center space-x-1.5 font-medium"
                          style={{ color: isDark ? (theme.accent || theme.primary) : theme.primary }}
                        >
                          {indexingProgress?.completed ? (
                            <>
                              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                              <span className="text-emerald-400 font-semibold">Indexing Complete!</span>
                            </>
                          ) : (
                            <>
                              <span
                                className="w-2 h-2 rounded-full animate-ping"
                                style={{ backgroundColor: theme.primary }}
                              />
                              <span>Indexing Documents in Background...</span>
                            </>
                          )}
                        </div>
                        <span
                          className="font-mono font-semibold"
                          style={{ color: isDark ? (theme.accent || theme.primary) : theme.primary }}
                        >
                          {indexingProgress?.percent != null
                            ? `${indexingProgress.percent}%`
                            : ragStatus?.indexer?.progress_percent != null
                            ? `${ragStatus.indexer.progress_percent}%`
                            : "Scanning..."}
                        </span>
                      </div>

                      {/* Progress Bar Track */}
                      <div className="w-full h-2 rounded-full bg-black/40 overflow-hidden border border-white/5">
                        <div
                          className={`h-full ${indexingProgress?.completed ? "bg-emerald-500" : ""} rounded-full transition-all duration-300 ease-out`}
                          style={{
                            background: indexingProgress?.completed ? undefined : theme.gradient,
                            width: `${Math.max(
                              4,
                              Math.min(100, indexingProgress?.percent ?? ragStatus?.indexer?.progress_percent ?? 0)
                            )}%`,
                          }}
                        />
                      </div>

                      {(indexingProgress?.current_file || ragStatus?.indexer?.current_file) && (
                        <div className="text-[10px] text-slate-400 truncate flex items-center space-x-1">
                          <FileText className="w-3 h-3 shrink-0 opacity-70" />
                          <span className="truncate">{indexingProgress?.current_file || ragStatus?.indexer?.current_file}</span>
                        </div>
                      )}
                    </div>
                  )}

                  {/* 3-Column Metrics Counters */}
                  <div className="grid grid-cols-3 gap-2 pt-1">
                    <div className={`p-2.5 rounded-lg border text-center ${isDark ? "bg-white/[0.02] border-white/5" : "bg-white border-black/5"}`}>
                      <div className="text-[10px] font-medium uppercase tracking-wider text-emerald-400 flex items-center justify-center space-x-1">
                        <CheckCircle2 className="w-3 h-3" />
                        <span>Done / Indexed</span>
                      </div>
                      <div className="text-sm font-semibold mt-0.5 font-mono">
                        {indexingProgress?.files_processed ?? ragStatus?.indexer?.files_processed ?? ragStatus?.documents ?? 0}
                      </div>
                      <div className="text-[9px] opacity-50">
                        {indexingProgress?.files_indexed != null ? `${indexingProgress.files_indexed} newly indexed` : `${ragStatus?.indexer?.files_indexed ?? 0} new files`}
                      </div>
                    </div>

                    <div className={`p-2.5 rounded-lg border text-center ${isDark ? "bg-white/[0.02] border-white/5" : "bg-white border-black/5"}`}>
                      <div className="text-[10px] font-medium uppercase tracking-wider text-amber-400 flex items-center justify-center space-x-1">
                        <Clock className="w-3 h-3" />
                        <span>Remaining</span>
                      </div>
                      <div className="text-sm font-semibold mt-0.5 font-mono">
                        {indexingProgress?.files_total != null ? Math.max(0, indexingProgress.files_total - indexingProgress.files_processed) : (ragStatus?.indexer?.files_left ?? 0)}
                      </div>
                      <div className="text-[9px] opacity-50">files left</div>
                    </div>

                    <div className={`p-2.5 rounded-lg border text-center ${isDark ? "bg-white/[0.02] border-white/5" : "bg-white border-black/5"}`}>
                      <div
                        className="text-[10px] font-medium uppercase tracking-wider flex items-center justify-center space-x-1"
                        style={{ color: isDark ? (theme.accent || theme.primary) : theme.primary }}
                      >
                        <Layers className="w-3 h-3" />
                        <span>Total Scanned</span>
                      </div>
                      <div className="text-sm font-semibold mt-0.5 font-mono">
                        {indexingProgress?.files_total ?? ragStatus?.indexer?.files_total ?? ragStatus?.documents ?? 0}
                      </div>
                      <div className="text-[9px] opacity-50">discovered files</div>
                    </div>
                  </div>

                  {/* Summary & Telemetry Footer */}
                  <div className="flex flex-wrap items-center justify-between gap-1 text-[10px] opacity-60 px-0.5 pt-1 border-t border-white/5">
                    <span>
                      Database: {ragStatus?.total ?? 0} indexed items ({ragStatus?.documents ?? 0} doc chunks, {ragStatus?.conversations ?? 0} memories)
                    </span>
                    {ragStatus?.indexer?.last_run && (
                      <span>
                        Last run: {new Date(ragStatus.indexer.last_run).toLocaleTimeString()} ({ragStatus.indexer.last_duration_seconds}s)
                      </span>
                    )}
                  </div>
                </div>

                {reindexMsg && (
                  <div
                    className={`text-[11px] px-2.5 py-1.5 rounded-lg flex items-center space-x-1.5 ${
                      reindexMsg.success
                        ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                        : "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                    }`}
                  >
                    {reindexMsg.success ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
                    <span>{reindexMsg.text}</span>
                  </div>
                )}

                <div className="h-px bg-white/5" />

                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-xs font-semibold">Interaction History</div>
                    <div className="text-[11px] opacity-60">
                      {historyCount > 0 ? `${historyCount} past interactions stored` : "No stored interactions"}
                    </div>
                  </div>
                  <div className="flex items-center space-x-2">
                    <DeleteButton
                      size="sm"
                      onConfirm={onClearHistory}
                      disabled={historyCount === 0}
                      title="Clear History"
                    />
                    <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Clear</span>
                  </div>
                </div>

                <div className="h-px bg-white/5" />

                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-xs font-semibold">Reset to Defaults</div>
                    <div className="text-[11px] opacity-60">Restore default color, visualizer, and clear inputs</div>
                  </div>
                  <button
                    onClick={handleResetToDefaults}
                    className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition-colors flex items-center space-x-1.5 ${
                      isDark
                        ? "bg-white/5 border-white/10 hover:bg-white/10 text-slate-300"
                        : "bg-black/5 border-black/10 hover:bg-black/10 text-slate-700"
                    }`}
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    <span>Reset Defaults</span>
                  </button>
                </div>
              </div>
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  </motion.div>
)}
</AnimatePresence>
  );
};
