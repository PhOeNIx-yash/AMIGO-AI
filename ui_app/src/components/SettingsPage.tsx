import React, { useState, useEffect, useMemo, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Settings,
  Palette,
  Server,
  CheckCircle2,
  AlertCircle,
  Sparkles,
  Moon,
  Sun,
  ArrowLeft,
  RotateCcw,
  Trash2,
  Activity,
  Check,
  Sliders,
  Type,
  LayoutTemplate,
  Sidebar,
  Minimize2,
  Maximize2,
  Clock,
  Orbit,
  Database,
  RefreshCw,
  FileText,
  Layers,
  Loader2,
  Brain,
  Volume2,
  Play,
  Square,
  User,
  Bot,
  Headphones,
} from "lucide-react";
import {
  BackendConfig,
  ColorTheme,
  PluginMode,
  VisualizerMode,
} from "../types";
import { KineticHeading, TextAnimationStyle } from "./KineticText";
import { COLOR_THEMES, GREETING_PRESETS } from "../data/presets";
import { testBackendConnection, fetchRagStatus, triggerRagReindex, RagStatusData } from "../services/assistantApi";

export interface VoiceOption {
  id: string;
  name: string;
  engine: "Kokoro";
  gender: "Female" | "Male" | "Robot";
  accent: "US" | "GB";
  desc: string;
  previewText: string;
  avatarGradient: string;
}

export const VOICES_CATALOG: VoiceOption[] = [
  // 5 Best Studio Female Neural Voices (Kokoro 24kHz)
  {
    id: "nicole",
    name: "Nicole",
    engine: "Kokoro",
    gender: "Female",
    accent: "US",
    desc: "Smooth, articulate & studio-clean American female (Recommended)",
    previewText: "Hello! I am Nicole, your clear and articulate studio voice.",
    avatarGradient: "from-rose-500 to-pink-600",
  },
  {
    id: "sarah",
    name: "Sarah",
    engine: "Kokoro",
    gender: "Female",
    accent: "US",
    desc: "Soft, natural & warm American female conversationalist",
    previewText: "Hello! I am Sarah, soft, warm and conversational.",
    avatarGradient: "from-fuchsia-500 to-rose-600",
  },
  {
    id: "heart",
    name: "Heart",
    engine: "Kokoro",
    gender: "Female",
    accent: "US",
    desc: "Warm, expressive and friendly conversational tone",
    previewText: "Hi there! I am Heart, warm, expressive and natural.",
    avatarGradient: "from-violet-500 to-purple-600",
  },
  {
    id: "sky",
    name: "Sky",
    engine: "Kokoro",
    gender: "Female",
    accent: "US",
    desc: "Bright, friendly & clear American female delivery",
    previewText: "Hey! I am Sky, bright, friendly and clear.",
    avatarGradient: "from-sky-400 to-blue-500",
  },
  {
    id: "bella",
    name: "Bella",
    engine: "Kokoro",
    gender: "Female",
    accent: "US",
    desc: "Crisp, energetic and articulate female delivery",
    previewText: "Hi there! I am Bella, crisp, energetic and ready to help.",
    avatarGradient: "from-amber-400 to-rose-500",
  },

  // 5 Best Studio Male Neural Voices (Kokoro 24kHz)
  {
    id: "adam",
    name: "Adam",
    engine: "Kokoro",
    gender: "Male",
    accent: "US",
    desc: "Deep, calm and grounded American baritone resonance",
    previewText: "Hello, I am Adam. Deep, calm and ready to assist you.",
    avatarGradient: "from-blue-600 to-indigo-700",
  },
  {
    id: "michael",
    name: "Michael",
    engine: "Kokoro",
    gender: "Male",
    accent: "US",
    desc: "Professional, crisp and articulate executive tone",
    previewText: "Greetings! I am Michael. Professional, clear and articulate.",
    avatarGradient: "from-cyan-600 to-blue-700",
  },
  {
    id: "echo",
    name: "Echo",
    engine: "Kokoro",
    gender: "Male",
    accent: "US",
    desc: "Warm, relatable and conversational American companion",
    previewText: "Hey there! I am Echo, warm and easy to talk to.",
    avatarGradient: "from-emerald-500 to-teal-700",
  },
  {
    id: "liam",
    name: "Liam",
    engine: "Kokoro",
    gender: "Male",
    accent: "US",
    desc: "Young, natural, and modern American male delivery",
    previewText: "Hey! I am Liam, young, natural and fast.",
    avatarGradient: "from-teal-500 to-cyan-600",
  },
  {
    id: "george",
    name: "George",
    engine: "Kokoro",
    gender: "Male",
    accent: "GB",
    desc: "Distinguished British English gentleman",
    previewText: "Good day! I am George, speaking distinguished British English.",
    avatarGradient: "from-amber-600 to-orange-700",
  },
];

interface SettingsPageProps {
  isOpen: boolean;
  onClose: () => void;
  isDark: boolean;
  onToggleTheme: () => void;
  colorTheme: ColorTheme;
  onChangeColorTheme: (theme: ColorTheme) => void;
  visualizerMode: VisualizerMode;
  onChangeVisualizerMode: (mode: VisualizerMode) => void;
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

  // Voice state
  const [selectedVoice, setSelectedVoice] = useState<string>(() => {
    return localStorage.getItem("amigo_selected_voice") || "nicole";
  });
  const [voiceFilter, setVoiceFilter] = useState<"all" | "female" | "male">("all");
  const [playingVoiceId, setPlayingVoiceId] = useState<string | null>(null);
  const previewTimerRef = useRef<any>(null);

  useEffect(() => {
    fetch("/api/settings")
      .then((res) => res.json())
      .then((data) => {
        const v =
          data?.user_profile?.preferences?.voice ||
          data?.ui_settings?.voice ||
          localStorage.getItem("amigo_selected_voice");
        if (v) {
          setSelectedVoice(v);
          localStorage.setItem("amigo_selected_voice", v);
        }
      })
      .catch(() => {
        const saved = localStorage.getItem("amigo_selected_voice");
        if (saved) setSelectedVoice(saved);
      });
  }, []);

  const handleSelectVoice = async (voiceId: string) => {
    setSelectedVoice(voiceId);
    localStorage.setItem("amigo_selected_voice", voiceId);
    try {
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_profile: { preferences: { voice: voiceId } },
          voice: voiceId,
        }),
      });
      const vObj = VOICES_CATALOG.find((v) => v.id === voiceId);
      const name = vObj ? vObj.name : voiceId;
      await fetch("/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: `${name} voice selected.` }),
      });
    } catch (e) {}
    setSavedBanner(true);
    setTimeout(() => setSavedBanner(false), 2000);
  };

  const handleTogglePlayPreview = async (e: React.MouseEvent, voice: VoiceOption) => {
    e.stopPropagation();
    if (playingVoiceId === voice.id) {
      if (previewTimerRef.current) {
        clearTimeout(previewTimerRef.current);
        previewTimerRef.current = null;
      }
      setPlayingVoiceId(null);
      try {
        await fetch("/api/action/execute", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ payload: { tool: "stop_speaking" } }),
        });
      } catch {}
      return;
    }

    if (previewTimerRef.current) {
      clearTimeout(previewTimerRef.current);
    }

    setPlayingVoiceId(voice.id);

    try {
      // 1. Immediately switch backend active voice to this preview voice
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_profile: { preferences: { voice: voice.id } },
          voice: voice.id,
        }),
      });

      // 2. Synthesize & play preview through Amigo's real offline neural TTS engine
      await fetch("/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: voice.previewText }),
      });

      // Reset animation once utterance finishes
      const durationMs = Math.max(3000, voice.previewText.length * 80);
      previewTimerRef.current = setTimeout(() => {
        setPlayingVoiceId(null);
        previewTimerRef.current = null;
      }, durationMs);
    } catch (err) {
      setPlayingVoiceId(null);
    }
  };

  const filteredVoices = useMemo(() => {
    if (voiceFilter === "female") {
      return VOICES_CATALOG.filter((v) => v.gender === "Female");
    }
    if (voiceFilter === "male") {
      return VOICES_CATALOG.filter((v) => v.gender === "Male");
    }
    return VOICES_CATALOG;
  }, [voiceFilter]);

  // Form states
  const [endpointUrl, setEndpointUrl] = useState(backendConfig.endpointUrl || "/api/assistant/process");
  const [actionWebhookUrl, setActionWebhookUrl] = useState(backendConfig.actionWebhookUrl || "");
  const [apiKey, setApiKey] = useState(backendConfig.apiKey || "");
  const [transcriptionEngine, setTranscriptionEngine] = useState(backendConfig.transcriptionEngine || "amigo-speech");
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

  const loadRagStatus = async () => {
    try {
      const stats = await fetchRagStatus();
      setRagStatus(stats);
      if (stats.indexer?.is_indexing) {
        setIsReindexing(true);
      } else if (!stats.is_indexing && !stats.indexer?.is_indexing && !indexingProgress?.is_indexing) {
        setIsReindexing(false);
      }
    } catch (e) {}
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

  useEffect(() => {
    if (!isOpen || activeTab !== "data") return;
    loadRagStatus();
    const isBusy = isReindexing || ragStatus?.is_indexing || ragStatus?.indexer?.is_indexing || indexingProgress?.is_indexing;
    const interval = setInterval(() => {
      loadRagStatus();
    }, isBusy ? 1000 : 4000);
    return () => clearInterval(interval);
  }, [isOpen, activeTab, isReindexing, ragStatus?.is_indexing, ragStatus?.indexer?.is_indexing, indexingProgress?.is_indexing]);

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

  // Sync state if config changes
  useEffect(() => {
    setEndpointUrl(backendConfig.endpointUrl || "/api/assistant/process");
    setActionWebhookUrl(backendConfig.actionWebhookUrl || "");
    setApiKey(backendConfig.apiKey || "");
    setTranscriptionEngine(backendConfig.transcriptionEngine || "web-speech");
    setAutoSpeech(backendConfig.autoSpeech !== false);
    setThinkingEnabled(backendConfig.thinkingEnabled === true);
  }, [backendConfig]);

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
    setSavedBanner(true);
    setTimeout(() => setSavedBanner(false), 2000);
  };

  const handleTestBackend = async () => {
    setTesting(true);
    setTestResult(null);

    const testConf: BackendConfig = {
      endpointUrl,
      actionWebhookUrl,
      apiKey,
      protocol: "rest",
      autoSpeech,
      transcriptionEngine,
    };

    const result = await testBackendConnection(testConf);
    setTestResult(result);
    setTesting(false);
  };

  const handleResetToDefaults = () => {
    onChangeColorTheme("violet");
    onChangeVisualizerMode("ribbon");
    onChangeTextAnimationStyle("silk_blur");
    onChangePluginMode("fullscreen");
    setTranscriptionEngine("amigo-speech");
    setAutoSpeech(true);
    setThinkingEnabled(false);
    setEndpointUrl("/api/assistant/process");
    setActionWebhookUrl("");
    setApiKey("");
    onResetAssistant();
    setSavedBanner(true);
    setTimeout(() => setSavedBanner(false), 2000);
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
          initial={{ opacity: 0, scale: 0.96, y: 16 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.96, y: 16 }}
          transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
          className={`absolute inset-0 z-50 flex flex-col backdrop-blur-2xl shadow-2xl overflow-hidden ${
            isDark ? "text-white" : "text-slate-900"
          }`}
          style={{
            transform: "translateZ(0)",
            background: isDark
              ? `radial-gradient(ellipse 120% 70% at 50% 0%, ${theme.primary}12 0%, rgba(11, 10, 23, 0.98) 70%)`
              : `radial-gradient(ellipse 120% 70% at 50% 0%, ${theme.primary}08 0%, rgba(248, 249, 252, 0.98) 70%)`,
          }}
        >
          {/* Header */}
          <div
            className={`flex items-center justify-between px-4 sm:px-6 py-3 border-b ${
              isDark ? "bg-black/40" : "bg-white/90"
            }`}
            style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
          >
            <div className="flex items-center space-x-3">
              <button
                id="settings-back-btn"
                onClick={onClose}
                className={`p-2 rounded-xl border transition-colors flex items-center justify-center ${
                  isDark
                    ? "bg-white/5 border-white/10 hover:bg-white/10 text-slate-200"
                    : "bg-black/5 border-black/10 hover:bg-black/10 text-slate-800"
                }`}
                title="Done & Close Settings"
              >
                <ArrowLeft className="w-4 h-4" />
              </button>
              <div>
                <div className="flex items-center space-x-2">
                  <Settings className="w-4 h-4" style={{ color: theme.accent }} />
                  <h2 className="text-base sm:text-lg font-semibold tracking-tight">Settings</h2>
                </div>
                <p className="text-xs opacity-60">Personalize themes, visualizers, and API settings</p>
              </div>
            </div>

            <div className="flex items-center space-x-2">
              {savedBanner && (
                <span className="text-xs font-semibold text-emerald-400 flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/20 animate-fade-in">
                  <Check className="w-3.5 h-3.5" />
                  <span>Saved</span>
                </span>
              )}

              <button
                id="settings-save-btn"
                onClick={handleSave}
                className="px-4 py-1.5 rounded-xl text-xs font-semibold text-white shadow-sm transition-transform active:scale-95"
                style={{
                  background: theme.gradient,
                }}
              >
                Save
              </button>
            </div>
          </div>

          {/* Main Layout */}
          <div className="flex-1 flex flex-col md:flex-row overflow-hidden">
            {/* Simple Tabs Sidebar */}
            <div
              className={`w-full md:w-52 flex-shrink-0 p-2 sm:p-3 border-b md:border-b-0 md:border-r flex md:flex-col space-x-1 md:space-x-0 md:space-y-1 ${
                isDark ? "bg-black/20" : "bg-slate-50"
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
                    className={`flex items-center space-x-2 px-3 py-2.5 rounded-xl text-xs font-medium transition-all ${
                      isActive
                        ? "font-semibold"
                        : isDark
                        ? "text-slate-400 hover:text-white hover:bg-white/5"
                        : "text-slate-600 hover:text-slate-900 hover:bg-black/5"
                    }`}
                    style={
                      isActive
                        ? {
                            borderLeft: `3px solid ${theme.primary}`,
                            backgroundColor: isDark ? `${theme.primary}18` : `${theme.primary}10`,
                            color: isDark ? (theme.accent || theme.primary) : theme.primary,
                          }
                        : {}
                    }
                  >
                    <Icon className="w-4 h-4 flex-shrink-0" />
                    <span>{tab.label}</span>
                  </button>
                );
              })}
            </div>

            {/* Content Area with smooth tab transition physics */}
            <div className="flex-1 overflow-y-auto p-4 sm:p-6 smooth-scroll-container">
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

                {/* Color Palette */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03]" : "bg-white"
                  }`}
                  style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                >
                  <div className="text-xs font-semibold uppercase tracking-wider opacity-60 mb-3">Accent Color</div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                    {(Object.keys(COLOR_THEMES) as ColorTheme[]).map((key) => {
                      const ct = COLOR_THEMES[key];
                      const isSelected = colorTheme === key;
                      return (
                        <button
                          key={key}
                          onClick={() => onChangeColorTheme(key)}
                          className={`p-2.5 rounded-xl border flex items-center space-x-2.5 transition-all text-left ${
                            isSelected
                              ? "shadow-sm font-medium"
                              : isDark
                              ? "border-white/10 bg-white/[0.02] hover:bg-white/5"
                              : "border-black/10 bg-white hover:bg-slate-50"
                          }`}
                          style={
                            isSelected
                              ? {
                                  borderColor: ct.primary,
                                  boxShadow: `0 0 0 1.5px ${ct.primary}40`,
                                  backgroundColor: `${ct.primary}12`,
                                }
                              : {}
                          }
                        >
                          <span
                            className="w-3.5 h-3.5 rounded-full flex-shrink-0 shadow-sm"
                            style={{ backgroundColor: ct.primary }}
                          />
                          <span className="text-xs font-medium truncate">{ct.name}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Thinking Orb Visual States */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03]" : "bg-white"
                  }`}
                  style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                >
                  <div className="flex items-start gap-3">
                    <div
                      className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl border"
                      style={{ color: theme.accent, borderColor: `${theme.accent}55`, backgroundColor: `${theme.accent}12` }}
                    >
                      <Orbit className="h-4 w-4" />
                    </div>
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-wider opacity-60">Thinking Orb States</div>
                      <p className="mt-1 text-[11px] leading-relaxed opacity-60">
                        Amigo uses a distinct orb motion for ready, listening, thinking, working, choices, and completed states.
                      </p>
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {["Breathing", "Listening", "Solving", "Working", "Connecting", "Weaving"].map((label) => (
                          <span
                            key={label}
                            className={`rounded-full border px-2 py-1 text-[10px] ${
                              isDark ? "border-white/10 bg-white/[0.03]" : "border-black/10 bg-black/[0.02]"
                            }`}
                          >
                            {label}
                          </span>
                        ))}
                      </div>
                    </div>
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
              <div className="space-y-4">
                {/* Active Voice Spotlight Banner */}
                {(() => {
                  const activeVoice = VOICES_CATALOG.find((v) => v.id === selectedVoice) || VOICES_CATALOG[0];
                  return (
                    <div
                      className={`p-4 rounded-2xl border relative overflow-hidden transition-all ${
                        isDark ? "bg-white/[0.03]" : "bg-white shadow-sm"
                      }`}
                      style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-3.5">
                          <div
                            className={`w-12 h-12 rounded-2xl bg-gradient-to-br ${activeVoice.avatarGradient} flex items-center justify-center text-white shadow-md flex-shrink-0`}
                          >
                            {activeVoice.gender === "Robot" ? (
                              <Bot className="w-6 h-6" />
                            ) : (
                              <User className="w-6 h-6" />
                            )}
                          </div>
                          <div>
                            <div className="flex items-center space-x-2">
                              <span className="text-sm font-semibold tracking-tight">{activeVoice.name}</span>
                              <span
                                className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                                  activeVoice.engine === "Kokoro"
                                    ? "bg-violet-500/15 text-violet-400 border border-violet-500/20"
                                    : "bg-blue-500/15 text-blue-400 border border-blue-500/20"
                                }`}
                              >
                                {activeVoice.engine} Neural
                              </span>
                              <span
                                className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-mono border"
                                style={{
                                  backgroundColor: `${theme.primary}18`,
                                  color: isDark ? (theme.accent || theme.primary) : theme.primary,
                                  borderColor: `${theme.primary}30`,
                                }}
                              >
                                Active Voice
                              </span>
                            </div>
                            <p className="text-xs opacity-60 mt-0.5">{activeVoice.desc}</p>
                          </div>
                        </div>

                        <button
                          type="button"
                          onClick={(e) => handleTogglePlayPreview(e, activeVoice)}
                          className={`p-2.5 rounded-xl border transition-all flex items-center justify-center ${
                            playingVoiceId === activeVoice.id
                              ? "bg-rose-500/20 border-rose-500/40 text-rose-300"
                              : isDark
                              ? "bg-white/5 border-white/10 hover:bg-white/10 text-white/80"
                              : "bg-black/5 border-black/10 hover:bg-black/10 text-slate-700"
                          }`}
                          title={playingVoiceId === activeVoice.id ? "Stop Preview" : "Preview Sample"}
                        >
                          {playingVoiceId === activeVoice.id ? (
                            <Square className="w-4 h-4 fill-current" />
                          ) : (
                            <Play className="w-4 h-4 fill-current" />
                          )}
                        </button>
                      </div>
                    </div>
                  );
                })()}

                {/* Engine / Category Filter Segmented Tabs */}
                <div className="flex items-center justify-between pt-1">
                  <div className="text-xs font-semibold uppercase tracking-wider opacity-60">Studio Neural Voices (24kHz)</div>
                  <div
                    className={`inline-flex p-1 rounded-xl border ${
                      isDark ? "bg-black/30" : "bg-slate-100"
                    }`}
                    style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
                  >
                    {(
                      [
                        { id: "all", label: "All (10)" },
                        { id: "female", label: "Female (5)" },
                        { id: "male", label: "Male (5)" },
                      ] as const
                    ).map((f) => (
                      <button
                        key={f.id}
                        type="button"
                        onClick={() => setVoiceFilter(f.id)}
                        className={`px-3 py-1 rounded-lg text-[11px] font-medium transition-all ${
                          voiceFilter === f.id
                            ? isDark
                              ? "bg-white/15 text-white font-semibold shadow-sm"
                              : "bg-white text-slate-900 font-semibold shadow-sm"
                            : "opacity-60 hover:opacity-100"
                        }`}
                      >
                        {f.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Voice Selection Cards Grid */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  {filteredVoices.map((voice) => {
                    const isSelected = selectedVoice === voice.id;
                    const isPlaying = playingVoiceId === voice.id;

                    return (
                      <div
                        key={voice.id}
                        onClick={() => handleSelectVoice(voice.id)}
                        className={`group relative p-3.5 rounded-2xl border text-left transition-all cursor-pointer select-none flex flex-col justify-between ${
                          isSelected
                            ? "shadow-sm"
                            : isDark
                            ? "border-white/10 bg-white/[0.02] hover:bg-white/[0.05] hover:border-white/20"
                            : "border-black/10 bg-white hover:bg-slate-50 hover:border-black/20 shadow-sm"
                        }`}
                        style={
                          isSelected
                            ? {
                                borderColor: `${theme.primary}80`,
                                backgroundColor: `${theme.primary}10`,
                                boxShadow: `0 0 0 1px ${theme.primary}30`,
                              }
                            : {}
                        }
                      >
                        <div>
                          <div className="flex items-start justify-between mb-2.5">
                            <div className="flex items-center space-x-2.5">
                              <div
                                className={`w-9 h-9 rounded-xl bg-gradient-to-br ${voice.avatarGradient} flex items-center justify-center text-white shadow-sm flex-shrink-0`}
                              >
                                {voice.gender === "Robot" ? (
                                  <Bot className="w-4 h-4" />
                                ) : (
                                  <User className="w-4 h-4" />
                                )}
                              </div>
                              <div>
                                <div className="text-xs font-semibold tracking-tight flex items-center space-x-1.5">
                                  <span>{voice.name}</span>
                                  {isSelected && (
                                    <CheckCircle2
                                      className="w-3.5 h-3.5"
                                      style={{ color: theme.accent || theme.primary }}
                                    />
                                  )}
                                </div>
                                <div className="text-[10px] opacity-60">
                                  {voice.gender} • {voice.accent}
                                </div>
                              </div>
                            </div>

                            <span
                              className="text-[9px] font-mono px-2 py-0.5 rounded-full font-medium bg-violet-500/15 text-violet-400 border border-violet-500/20"
                            >
                              Kokoro 24kHz
                            </span>
                          </div>

                          <p className="text-[11px] opacity-70 leading-relaxed mb-3">{voice.desc}</p>
                        </div>

                        {/* Bottom Actions Row */}
                        <div className="flex items-center justify-between pt-2 border-t border-white/5">
                          <button
                            type="button"
                            onClick={(e) => handleTogglePlayPreview(e, voice)}
                            className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all ${
                              isPlaying
                                ? "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                                : isDark
                                ? "bg-white/5 hover:bg-white/10 text-white/80 border border-white/10"
                                : "bg-black/5 hover:bg-black/10 text-slate-700 border border-black/10"
                            }`}
                          >
                            {isPlaying ? (
                              <>
                                <Square className="w-3 h-3 fill-current" />
                                <span>Stop</span>
                              </>
                            ) : (
                              <>
                                <Play className="w-3 h-3 fill-current" />
                                <span>Preview</span>
                              </>
                            )}
                          </button>

                          <span
                            className={`text-[10px] font-medium ${
                              isSelected ? "font-semibold" : "opacity-40"
                            }`}
                            style={isSelected ? { color: isDark ? (theme.accent || theme.primary) : theme.primary } : {}}
                          >
                            {isSelected ? "Selected" : "Click to select"}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
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
                  <div className="flex items-center justify-between">
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
                      className="px-3 py-1.5 rounded-xl text-xs font-semibold border disabled:opacity-50 transition-colors flex items-center space-x-1.5 shadow-sm"
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
                  <button
                    onClick={onClearHistory}
                    disabled={historyCount === 0}
                    className="px-3 py-1.5 rounded-xl text-xs font-semibold border border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20 disabled:opacity-30 transition-colors flex items-center space-x-1.5"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>Clear History</span>
                  </button>
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
