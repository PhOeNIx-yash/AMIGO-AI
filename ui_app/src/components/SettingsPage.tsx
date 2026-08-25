import React, { useState, useEffect, useMemo } from "react";
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
  Waves,
} from "lucide-react";
import {
  BackendConfig,
  ColorTheme,
  PluginMode,
  VisualizerMode,
} from "../types";
import { TextAnimationStyle } from "./KineticText";
import { COLOR_THEMES, GREETING_PRESETS } from "../data/presets";
import { testBackendConnection } from "../services/assistantApi";

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

type TabType = "appearance" | "backend" | "data";

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

  // Form states
  const [endpointUrl, setEndpointUrl] = useState(backendConfig.endpointUrl || "/api/assistant/process");
  const [actionWebhookUrl, setActionWebhookUrl] = useState(backendConfig.actionWebhookUrl || "");
  const [apiKey, setApiKey] = useState(backendConfig.apiKey || "");
  const [transcriptionEngine, setTranscriptionEngine] = useState(backendConfig.transcriptionEngine || "web-speech");
  const [autoSpeech, setAutoSpeech] = useState(backendConfig.autoSpeech !== false);

  // Diagnostics
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success?: boolean;
    latencyMs?: number;
    message?: string;
  } | null>(null);

  const [savedBanner, setSavedBanner] = useState(false);

  // Sync state if config changes
  useEffect(() => {
    setEndpointUrl(backendConfig.endpointUrl || "/api/assistant/process");
    setActionWebhookUrl(backendConfig.actionWebhookUrl || "");
    setApiKey(backendConfig.apiKey || "");
    setTranscriptionEngine(backendConfig.transcriptionEngine || "web-speech");
    setAutoSpeech(backendConfig.autoSpeech !== false);
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
    onChangeTextAnimationStyle("amazing_fluid");
    onChangePluginMode("fullscreen");
    setTranscriptionEngine("web-speech");
    setAutoSpeech(true);
    setEndpointUrl("/api/assistant/process");
    setActionWebhookUrl("");
    setApiKey("");
    onResetAssistant();
    setSavedBanner(true);
    setTimeout(() => setSavedBanner(false), 2000);
  };

  const tabs = [
    { id: "appearance", label: "Appearance", icon: Palette },
    { id: "backend", label: "AI & Endpoint", icon: Server },
    { id: "data", label: "Data & Reset", icon: Sliders },
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
            isDark ? "bg-[#0b0a17]/98 text-white" : "bg-[#f8f9fc]/98 text-slate-900"
          }`}
          style={{ transform: "translateZ(0)" }}
        >
          {/* Header */}
          <div
            className={`flex items-center justify-between px-4 sm:px-6 py-3 border-b ${
              isDark ? "border-white/10 bg-black/40" : "border-black/10 bg-white/90"
            }`}
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
                isDark ? "border-white/10 bg-black/20" : "border-black/10 bg-slate-50"
              }`}
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
                        ? isDark
                          ? "bg-white/10 text-white font-semibold"
                          : "bg-black/10 text-slate-900 font-semibold"
                        : isDark
                        ? "text-slate-400 hover:text-white hover:bg-white/5"
                        : "text-slate-600 hover:text-slate-900 hover:bg-black/5"
                    }`}
                    style={isActive ? { borderLeft: `3px solid ${theme.primary}` } : {}}
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
                    isDark ? "bg-white/[0.03] border-white/10" : "bg-white border-black/10"
                  }`}
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
                          ? "border-emerald-500 ring-2 ring-emerald-500/30 bg-white text-slate-900 shadow-sm"
                          : "border-white/10 bg-white/5 opacity-70 hover:opacity-100 text-white"
                      }`}
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
                          ? "border-emerald-500 ring-2 ring-emerald-500/30 bg-black/60 text-white shadow-sm"
                          : "border-black/10 bg-black/5 opacity-70 hover:opacity-100 text-slate-900"
                      }`}
                    >
                      <Moon className="w-5 h-5 text-indigo-400 flex-shrink-0" />
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
                    isDark ? "bg-white/[0.03] border-white/10" : "bg-white border-black/10"
                  }`}
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
                              ? "border-emerald-500 ring-1 ring-emerald-500/40 bg-emerald-500/5 shadow-sm"
                              : isDark
                              ? "border-white/10 bg-white/[0.02] hover:bg-white/5"
                              : "border-black/10 bg-white hover:bg-slate-50"
                          }`}
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

                {/* Waveform Visualizer Style */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03] border-white/10" : "bg-white border-black/10"
                  }`}
                >
                  <div className="text-xs font-semibold uppercase tracking-wider opacity-60 mb-3">Audio Waveform Style</div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {[
                      { id: "ribbon", name: "Ribbon Wave", desc: "Fluid listening wave" },
                      { id: "orb", name: "Particle Orb", desc: "Thinking & working 3D core" },
                    ].map((v) => {
                      const isSelected = visualizerMode === v.id;
                      return (
                        <button
                          key={v.id}
                          onClick={() => onChangeVisualizerMode(v.id as VisualizerMode)}
                          className={`p-2.5 rounded-xl border text-left transition-all ${
                            isSelected
                              ? "border-emerald-500 ring-1 ring-emerald-500/40 bg-emerald-500/5 shadow-sm"
                              : isDark
                              ? "border-white/10 bg-white/[0.02] hover:bg-white/5"
                              : "border-black/10 bg-white hover:bg-slate-50"
                          }`}
                        >
                          <div className="flex items-center space-x-1.5 mb-0.5">
                            <Waves className="w-3.5 h-3.5" style={{ color: theme.accent }} />
                            <div className="text-xs font-semibold">{v.name}</div>
                          </div>
                          <div className="text-[10px] opacity-60">{v.desc}</div>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Greeting Headline & Prompt Options */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03] border-white/10" : "bg-white border-black/10"
                  } space-y-3`}
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
                          ? "border-emerald-500 bg-emerald-500/10 text-emerald-400"
                          : isDark
                          ? "border-white/10 bg-white/5 text-slate-400 hover:text-slate-200"
                          : "border-black/10 bg-black/5 text-slate-600 hover:text-slate-900"
                      }`}
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
                        isDark ? "bg-white/[0.02] border-white/10" : "bg-black/[0.02] border-black/10"
                      }`}
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
                          ? "bg-black/40 border-white/10 text-white focus:border-indigo-500"
                          : "bg-slate-50 border-black/10 text-slate-900 focus:border-indigo-500"
                      }`}
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
                                ? "border-emerald-500 bg-emerald-500/10 text-emerald-400 font-medium"
                                : isDark
                                ? "border-white/[0.08] bg-white/[0.02] text-slate-300 hover:bg-white/5 hover:text-white"
                                : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                            }`}
                          >
                            <span className="truncate max-w-[200px]">{preset.text}</span>
                            {isSelected && <Check className="w-3 h-3 text-emerald-400 flex-shrink-0" />}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>

                {/* Text Animation Style (AmazingUI) */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03] border-white/10" : "bg-white border-black/10"
                  }`}
                >
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-wider opacity-60">Typography & Animation Style</div>
                      <div className="text-[11px] opacity-60">Live kinetic physics for greetings and assistant speech</div>
                    </div>
                    <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-indigo-500/20 text-indigo-400 font-mono">10 Styles</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {[
                      { id: "amazing_fluid", label: "Fluid Wave Spring", desc: "Per-letter liquid rise & spring settle" },
                      { id: "aurora_glow", label: "Aurora Borealis Glow", desc: "Multi-chromatic rainbow ambient sheen" },
                      { id: "floating_lift", label: "Floating Elevation", desc: "Anti-gravity floating 3D spatial hover" },
                      { id: "kinetic_wave", label: "Sine Wave Float", desc: "Continuous rolling crest amplitude" },
                      { id: "neon_pulse", label: "Cyber Neon Pulse", desc: "High-intensity luminescent pulsation" },
                      { id: "stagger_cascade", label: "3D Perspective Flip", desc: "Rotational cascade with spring dampening" },
                      { id: "elastic_bounce", label: "Elastic Liquid Pop", desc: "Playful elasticity & fluid stretch" },
                      { id: "gradient_shine", label: "Luminous Gradient Sheen", desc: "Light sweep across typography" },
                      { id: "blur_reveal", label: "Gaussian Blur-to-Focus", desc: "Deep cinematic lens focus" },
                      { id: "hologram_typewriter", label: "Holographic Token Stream", desc: "Digital token streaming reveal" },
                    ].map((styleItem) => {
                      const isSelected = textAnimationStyle === styleItem.id;
                      return (
                        <button
                          key={styleItem.id}
                          onClick={() => onChangeTextAnimationStyle(styleItem.id as TextAnimationStyle)}
                          className={`p-2.5 rounded-xl border text-left transition-all ${
                            isSelected
                              ? "border-emerald-500 ring-1 ring-emerald-500/40 bg-emerald-500/5 shadow-sm"
                              : isDark
                              ? "border-white/10 bg-white/[0.02] hover:bg-white/5"
                              : "border-black/10 bg-white hover:bg-slate-50"
                          }`}
                        >
                          <div className="flex items-center justify-between mb-0.5">
                            <div className="flex items-center space-x-1.5 text-xs font-semibold">
                              <Type className="w-3.5 h-3.5" style={{ color: theme.accent }} />
                              <span>{styleItem.label}</span>
                            </div>
                            {isSelected && <Check className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />}
                          </div>
                          <div className="text-[10px] opacity-60">{styleItem.desc}</div>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Plugin Docking / Window Layout */}
                <div
                  className={`p-4 rounded-2xl border ${
                    isDark ? "bg-white/[0.03] border-white/10" : "bg-white border-black/10"
                  }`}
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
                              ? "border-emerald-500 ring-1 ring-emerald-500/40 bg-emerald-500/5 shadow-sm"
                              : isDark
                              ? "border-white/10 bg-white/[0.02] hover:bg-white/5"
                              : "border-black/10 bg-white hover:bg-slate-50"
                          }`}
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

            {/* 2. BACKEND & AI TAB */}
            {activeTab === "backend" && (
              <div
                className={`p-4 rounded-2xl border ${
                  isDark ? "bg-white/[0.03] border-white/10" : "bg-white border-black/10"
                } space-y-4`}
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
                  />
                </div>

                {/* Diagnostics Test Button */}
                <div className="pt-2 flex flex-wrap items-center justify-between gap-2 border-t border-white/5">
                  <button
                    onClick={handleTestBackend}
                    disabled={testing}
                    className="px-3.5 py-1.5 rounded-xl text-xs font-semibold border border-indigo-500/30 bg-indigo-500/10 text-indigo-400 hover:bg-indigo-500/20 transition-colors flex items-center space-x-1.5"
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
                  isDark ? "bg-white/[0.03] border-white/10" : "bg-white border-black/10"
                } space-y-4`}
              >
                <div className="text-xs font-semibold uppercase tracking-wider opacity-60">History & Reset</div>

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
