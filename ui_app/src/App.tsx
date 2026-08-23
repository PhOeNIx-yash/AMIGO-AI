import React, { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  AssistantState,
  VisualizerMode,
  ColorTheme,
  AssistantResponse,
  ContactItem,
  PluginMode,
  HistoryEntry,
  BackendConfig,
  ActionCardItem,
} from "./types";
import { CanvasVisualizer } from "./components/CanvasVisualizer";
import { TitleBar } from "./components/TitleBar";
import { ActionCard } from "./components/ActionCard";
import { ContactPicker } from "./components/ContactPicker";
import { VoiceControls } from "./components/VoiceControls";
import { HistoryPanel } from "./components/HistoryPanel";
import { BackendSettingsModal } from "./components/BackendSettingsModal";
import { SettingsPage } from "./components/SettingsPage";
import { KineticHeading, KineticStateBadge, TextAnimationStyle } from "./components/KineticText";
import { IntentBridgeHUD, isActionIntent } from "./components/IntentBridgeHUD";
import { COLOR_THEMES, GREETING_PRESETS } from "./data/presets";
import { speakText, sfx } from "./utils/audio";
import { processVoiceCommand, executeBackendAction } from "./services/assistantApi";
import { Sparkles, ChevronUp, Shuffle } from "lucide-react";

export default function App() {
  const [isDark, setIsDark] = useState<boolean>(true);
  const [soundEnabled, setSoundEnabled] = useState<boolean>(true);
  const [colorTheme, setColorTheme] = useState<ColorTheme>("violet");
  const [visualizerMode, setVisualizerMode] = useState<VisualizerMode>("ribbon");
  const [textAnimationStyle, setTextAnimationStyle] = useState<TextAnimationStyle>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_anim_style");
      if (saved) return saved as TextAnimationStyle;
    }
    return "amazing_fluid";
  });
  const [pluginMode, setPluginMode] = useState<PluginMode>("fullscreen");
  const [isOpen, setIsOpen] = useState<boolean>(true);
  const [showHistory, setShowHistory] = useState<boolean>(false);
  const [showBackendModal, setShowBackendModal] = useState<boolean>(false);
  const [showSettings, setShowSettings] = useState<boolean>(false);

  // Greeting configuration
  const [greetingText, setGreetingText] = useState<string>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_greeting");
      if (saved) return saved;
    }
    return "What can I help you with ?";
  });

  const [autoCycleGreetings, setAutoCycleGreetings] = useState<boolean>(true);
  const [autoCycleInterval, setAutoCycleInterval] = useState<number>(() => {
    if (typeof window !== "undefined") {
      try {
        const saved = localStorage.getItem("windows11_voice_assistant_autocycle_interval");
        if (saved) {
          const parsed = parseInt(saved, 10);
          if (!isNaN(parsed) && parsed >= 3 && parsed <= 60) return parsed;
        }
      } catch (e) {}
    }
    return 10;
  });
  const [greetingIndex, setGreetingIndex] = useState<number>(0);

  // Save auto cycle interval
  useEffect(() => {
    try {
      localStorage.setItem("windows11_voice_assistant_autocycle_interval", String(autoCycleInterval));
    } catch (e) {}
  }, [autoCycleInterval]);

  // Save text animation style
  useEffect(() => {
    try {
      localStorage.setItem("windows11_voice_assistant_anim_style", textAnimationStyle);
    } catch (e) {}
  }, [textAnimationStyle]);

  // Save greeting text
  useEffect(() => {
    try {
      localStorage.setItem("windows11_voice_assistant_greeting", greetingText);
    } catch (e) {}
  }, [greetingText]);

  // Save auto cycle greetings
  useEffect(() => {
    try {
      localStorage.setItem("windows11_voice_assistant_autocycle_greeting", String(autoCycleGreetings));
    } catch (e) {}
  }, [autoCycleGreetings]);

  // Persistent Backend configuration for plug-and-play connection
  const [backendConfig, setBackendConfig] = useState<BackendConfig>(() => {
    if (typeof window !== "undefined") {
      try {
        const saved = localStorage.getItem("assistant_backend_config");
        if (saved) {
          const parsed = JSON.parse(saved);
          if (parsed && typeof parsed === "object") return parsed;
        }
      } catch (e) {}
    }
    return {
      endpointUrl: "http://127.0.0.1:5000/api/assistant/process",
      actionWebhookUrl: "http://127.0.0.1:5000/api/action/execute",
      apiKey: "",
      customHeaders: "",
      protocol: "rest",
      autoSpeech: true,
      transcriptionEngine: "web-speech",
    };
  });

  // Save backend config and sync settings with Amigo backend
  const handleSaveBackendConfig = async (config: BackendConfig) => {
    setBackendConfig(config);
    try {
      localStorage.setItem("assistant_backend_config", JSON.stringify(config));
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          theme: isDark ? "dark" : "light",
          autoSpeech: config.autoSpeech,
        }),
      });
    } catch (e) {}
  };

  const isBackendCustom = Boolean(
    backendConfig.endpointUrl && backendConfig.endpointUrl !== "/api/assistant/process"
  );

  const [history, setHistory] = useState<HistoryEntry[]>([]);

  // Fetch conversation history directly from amigo_memory.json on mount
  const fetchBackendHistory = async () => {
    try {
      const res = await fetch("/api/history");
      if (res.ok) {
        const memory = await res.json();
        if (memory && Array.isArray(memory.conversations)) {
          const formatted: HistoryEntry[] = memory.conversations.map((c: any, idx: number) => ({
            id: `hist-${idx}-${new Date(c.timestamp || Date.now()).getTime()}`,
            prompt: c.user || "",
            timestamp: new Date(c.timestamp || Date.now()).getTime(),
            status: "completed",
            response: {
              speechReply: c.assistant || "",
              displayTitle: c.user || "",
              intent: c.tool || "chat",
              requiresDisambiguation: false,
              actionCards: [],
              executionSummary: {
                status: "completed",
                headline: "Executed with Amigo",
                details: c.assistant || "",
              },
            },
          }));
          setHistory(formatted.reverse());
        }
      }
    } catch (e) {}
  };

  useEffect(() => {
    fetchBackendHistory();
  }, []);

  const [state, setState] = useState<AssistantState>("idle");
  const [activePrompt, setActivePrompt] = useState<string>("");
  const [displayText, setDisplayText] = useState<string>(greetingText);
  const [isListening, setIsListening] = useState<boolean>(false);
  const [liveTranscript, setLiveTranscript] = useState<string>("");

  const [assistantData, setAssistantData] = useState<AssistantResponse | null>(null);
  const [selectedContact, setSelectedContact] = useState<ContactItem | undefined>(undefined);
  const [loading, setLoading] = useState<boolean>(false);

  // Real-time bidirectional SSE sync with Amigo Python voice loop & server events
  useEffect(() => {
    let es: EventSource | null = null;
    const connectSSE = () => {
      try {
        es = new EventSource("/events");
        es.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === "state_change") {
              const s = data.state;
              if (s === "listening") {
                setIsListening(true);
                setState("listening");
              } else if (s === "idle") {
                setIsListening(false);
                setState("idle");
              } else if (s === "speaking") {
                setState("completed");
              }
            } else if (data.type === "chat_message") {
              if (data.sender === "user") {
                setActivePrompt(data.text);
                setDisplayText(data.text);
                setState("processing");
              } else if (data.sender === "assistant" || data.sender === "amigo") {
                setDisplayText(data.text);
                setState("completed");
                fetchBackendHistory();
              }
            } else if (data.type === "intent_detected") {
              setState("working");
            } else if (data.type === "history_cleared") {
              setHistory([]);
            }
          } catch (e) {}
        };
        es.onerror = () => {
          es?.close();
          setTimeout(connectSSE, 3000);
        };
      } catch (err) {}
    };
    connectSSE();
    return () => {
      es?.close();
    };
  }, []);

  // Dynamic auto-cycling of greeting phrases when idle
  useEffect(() => {
    if (!autoCycleGreetings || state !== "idle") return;

    const interval = setInterval(() => {
      setGreetingIndex((prevIndex) => (prevIndex + 1) % GREETING_PRESETS.length);
    }, autoCycleInterval * 1000);

    return () => clearInterval(interval);
  }, [autoCycleGreetings, state, autoCycleInterval]);

  // Update greeting text and display text when greetingIndex or state changes
  useEffect(() => {
    if (state === "idle") {
      if (autoCycleGreetings) {
        const activeText = GREETING_PRESETS[greetingIndex]?.text || GREETING_PRESETS[0].text;
        setGreetingText(activeText);
        setDisplayText(activeText);
      } else {
        setDisplayText(greetingText);
      }
    }
  }, [greetingIndex, autoCycleGreetings, state, greetingText]);

  // Quick cycle to next greeting phrase on click
  const handleShuffleGreeting = () => {
    sfx.playClick();
    setGreetingIndex((prevIndex) => (prevIndex + 1) % GREETING_PRESETS.length);
  };

  // Set listening state with synchronized assistant state
  const handleSetListening = (listening: boolean) => {
    setIsListening(listening);
    if (listening) {
      setState("listening");
    } else if (state === "listening" && !loading) {
      setState("idle");
    }
  };

  // Sync sound settings with utility
  const handleToggleSound = () => {
    const next = !soundEnabled;
    setSoundEnabled(next);
    sfx.setEnabled(next);
  };

  // Reset conversation to initial state
  const handleReset = useCallback(() => {
    setState("idle");
    setActivePrompt("");
    setDisplayText(greetingText);
    setAssistantData(null);
    setSelectedContact(undefined);
    setIsListening(false);
    setLoading(false);
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
  }, [greetingText]);

  // Process user voice or typed command
  const handleProcessCommand = async (prompt: string) => {
    if (!prompt.trim()) return;

    setActivePrompt(prompt);
    setDisplayText(prompt);
    setState("processing");
    setLoading(true);

    try {
      const data = await processVoiceCommand(prompt, backendConfig);
      setAssistantData(data);
      setLoading(false);

      // Update center stage with the full real assistant response text
      const resultText = data.speechReply || data.executionSummary?.details || data.displayTitle || prompt;
      setDisplayText(resultText);

      // Append to command history
      const newEntry: HistoryEntry = {
        id: `hist-${Date.now()}`,
        timestamp: Date.now(),
        prompt: prompt.trim(),
        response: data,
      };
      setHistory((prev) => [
        newEntry,
        ...prev.filter((p) => p.prompt.toLowerCase() !== prompt.trim().toLowerCase()),
      ]);

      if (data.actionCards && data.actionCards.length > 0) {
        setState("action_card");
      } else if (data.requiresDisambiguation && data.contactMatches && data.contactMatches.length > 0) {
        setState("contact_picker");
        if (data.disambiguationQuestion) {
          setDisplayText(data.disambiguationQuestion);
        }
      } else {
        setState("completed");
      }
    } catch (err) {
      console.warn("Assistant processing fallback:", err);
      setLoading(false);
      setState("completed");
    }
  };

  // Re-run previous voice command from history
  const handleReRunHistoryCommand = (prompt: string) => {
    setShowHistory(false);
    handleProcessCommand(prompt);
  };

  // Inspect previous result from history
  const handleSelectHistoryEntry = (entry: HistoryEntry) => {
    setActivePrompt(entry.prompt);
    setAssistantData(entry.response);
    setSelectedContact(entry.selectedContact);
    const fullText = entry.response.speechReply || entry.response.executionSummary?.details || entry.response.displayTitle || entry.prompt;
    setDisplayText(fullText);
    setState(entry.response.requiresDisambiguation ? "contact_picker" : "completed");
    setShowHistory(false);
    if (entry.response.speechReply && backendConfig.autoSpeech !== false && soundEnabled) {
      speakText(entry.response.speechReply);
    }
  };

  // Clear all command history across UI and Amigo memory backend
  const handleClearHistory = async () => {
    sfx.playClick();
    setHistory([]);
    try {
      localStorage.removeItem("windows11_voice_assistant_history");
      await fetch("/api/clear-memory", { method: "POST" });
    } catch (e) {}
  };

  // Toggle items on the action card
  const handleToggleActionItem = (id: string) => {
    if (!assistantData) return;
    setAssistantData({
      ...assistantData,
      actionCards: assistantData.actionCards.map((c) =>
        c.id === id ? { ...c, selected: !c.selected } : c
      ),
    });
  };

  // Execute a single button action on an action card
  const handleExecuteSingleAction = async (item: ActionCardItem) => {
    sfx.playClick();
    await executeBackendAction(item, backendConfig);
    setDisplayText(`Executed: ${item.title}`);
    setState("completed");
  };

  // User confirms the action cards
  const handleConfirmActions = async () => {
    setDisplayText("OK! Great! Working on this...");
    setState("working");

    // Dispatch webhook to backend for selected actions
    if (assistantData?.actionCards) {
      const selectedActions = assistantData.actionCards.filter((a) => a.selected);
      for (const act of selectedActions) {
        executeBackendAction(act, backendConfig).catch(() => {});
      }
    }

    // If contact disambiguation is required
    if (assistantData?.requiresDisambiguation && (assistantData.contactMatches?.length || 0) > 0) {
      setTimeout(() => {
        const question =
          assistantData.disambiguationQuestion ||
          "There are several matches. Which would you like me to use?";
        setDisplayText(question);
        setState("contact_picker");
        if (backendConfig.autoSpeech !== false && soundEnabled) {
          speakText(question);
        }
      }, 1400);
    } else {
      // Direct completion
      setTimeout(() => {
        sfx.playSuccess();
        setState("completed");
      }, 1600);
    }
  };

  // User selects a specific contact from the disambiguation list
  const handleSelectContact = (contact: ContactItem) => {
    setSelectedContact(contact);
    setDisplayText("OK! Great! Working on this...");
    setState("working");

    setTimeout(() => {
      sfx.playSuccess();
      setState("completed");
      if (backendConfig.autoSpeech !== false && soundEnabled) {
        speakText(`Selected ${contact.name}.`);
      }
    }, 1500);
  };

  const activeThemeObj = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;

  // Plugin container layout styling based on pluginMode
  const getPluginContainerClasses = () => {
    if (!isOpen) return "hidden";

    switch (pluginMode) {
      case "docked_right":
        return "fixed top-0 right-0 h-screen w-full sm:w-[460px] shadow-2xl z-50 border-l border-white/10";
      case "docked_bottom":
        return "fixed bottom-0 left-1/2 -translate-x-1/2 w-full max-w-4xl h-[560px] rounded-t-3xl shadow-2xl z-50 border-t border-x border-white/10";
      case "floating":
        return "fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[94vw] max-w-4xl h-[86vh] max-h-[820px] rounded-3xl shadow-2xl z-50 border border-white/15 overflow-hidden";
      case "fullscreen":
      default:
        return "fixed inset-0 w-screen h-screen z-50";
    }
  };

  return (
    <div className="relative w-screen h-screen overflow-hidden bg-slate-950 flex items-center justify-center">
      {/* Background Desktop Simulation for Plugin Context */}
      <div className="absolute inset-0 z-0 flex flex-col justify-between p-6 opacity-30 select-none pointer-events-none">
        <div className="flex items-center justify-between text-xs text-white/50">
          <div className="flex items-center space-x-2 font-mono">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
            <span>Host Workspace • Plugin Ready</span>
          </div>
          <div className="text-white/40">Windows 11 Voice Assistant Plugin</div>
        </div>
        <div className="text-center text-white/20 text-sm font-light">
          Assistant is active as a plugin. You can toggle floating, docked right sidebar, or docked bottom modes.
        </div>
        <div className="flex justify-end text-xs text-white/30">v1.3.0 • AI Studio</div>
      </div>

      {/* Floating Trigger Button when Plugin is Minimized */}
      {!isOpen && (
        <motion.button
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0, opacity: 0 }}
          whileHover={{ scale: 1.08 }}
          whileTap={{ scale: 0.95 }}
          onClick={() => {
            sfx.playWakeChime();
            setIsOpen(true);
          }}
          className="fixed bottom-6 right-6 z-50 flex items-center space-x-2.5 px-4 py-3 rounded-full text-white shadow-2xl border border-white/20"
          style={{
            background: activeThemeObj.gradient,
            boxShadow: `0 12px 30px ${activeThemeObj.glow}`,
          }}
        >
          <div className="relative flex items-center justify-center">
            <Sparkles className="w-5 h-5 animate-pulse" />
          </div>
          <span className="text-xs font-semibold tracking-wide">Open Voice Assistant</span>
          <ChevronUp className="w-4 h-4" />
        </motion.button>
      )}

      {/* Main Assistant Plugin Window */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            id="windows-voice-assistant-plugin"
            initial={{
              opacity: 0,
              scale: pluginMode === "floating" ? 0.94 : 1,
              y: pluginMode === "docked_bottom" ? 100 : 0,
            }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
            className={`flex flex-col transition-colors duration-500 ${
              isDark ? `${activeThemeObj.bgDark} text-white` : `${activeThemeObj.bgLight} text-slate-900`
            } ${getPluginContainerClasses()}`}
          >
            {/* Dynamic Hardware-Accelerated Ambient Glow Background */}
            <div
              className="absolute inset-0 pointer-events-none z-0 transition-opacity duration-300 opacity-25"
              style={{
                background: isDark
                  ? `radial-gradient(circle 450px at 30% 20%, ${activeThemeObj.primary}, transparent 70%), radial-gradient(circle 400px at 80% 60%, ${activeThemeObj.secondary}, transparent 70%), radial-gradient(circle 500px at 40% 90%, ${activeThemeObj.accent}, transparent 70%)`
                  : `radial-gradient(circle 450px at 30% 20%, ${activeThemeObj.primary}33, transparent 70%), radial-gradient(circle 400px at 80% 60%, ${activeThemeObj.secondary}33, transparent 70%)`,
                transform: "translateZ(0)",
                willChange: "opacity",
              }}
            />

            {/* Windows 11 Fluent TitleBar Header */}
            <TitleBar
              isDark={isDark}
              onToggleTheme={() => setIsDark(!isDark)}
              soundEnabled={soundEnabled}
              onToggleSound={handleToggleSound}
              colorTheme={colorTheme}
              onChangeColorTheme={setColorTheme}
              visualizerMode={visualizerMode}
              onChangeVisualizerMode={setVisualizerMode}
              textAnimationStyle={textAnimationStyle}
              onChangeTextAnimationStyle={setTextAnimationStyle}
              onReset={handleReset}
              pluginMode={pluginMode}
              onChangePluginMode={setPluginMode}
              isOpen={isOpen}
              onToggleOpen={() => setIsOpen(!isOpen)}
              showHistory={showHistory}
              onToggleHistory={() => setShowHistory(!showHistory)}
              historyCount={history.length}
              onOpenBackendSettings={() => setShowBackendModal(true)}
              isBackendCustom={isBackendCustom}
              onOpenSettings={() => setShowSettings(true)}
            />

            {/* Primary Workspace Area with Visualizer, Dynamic Content & History Sidebar */}
            <main className="relative flex-1 flex flex-col justify-between items-center z-10 w-full min-h-0 overflow-hidden">
              {/* Settings Page View / Full Modal */}
              <SettingsPage
                isOpen={showSettings}
                onClose={() => setShowSettings(false)}
                isDark={isDark}
                onToggleTheme={() => setIsDark(!isDark)}
                colorTheme={colorTheme}
                onChangeColorTheme={setColorTheme}
                visualizerMode={visualizerMode}
                onChangeVisualizerMode={setVisualizerMode}
                textAnimationStyle={textAnimationStyle}
                onChangeTextAnimationStyle={setTextAnimationStyle}
                greetingText={greetingText}
                onChangeGreetingText={setGreetingText}
                autoCycleGreetings={autoCycleGreetings}
                onToggleAutoCycleGreetings={() => setAutoCycleGreetings(!autoCycleGreetings)}
                autoCycleInterval={autoCycleInterval}
                onChangeAutoCycleInterval={setAutoCycleInterval}
                pluginMode={pluginMode}
                onChangePluginMode={setPluginMode}
                soundEnabled={soundEnabled}
                onToggleSound={handleToggleSound}
                backendConfig={backendConfig}
                onSaveBackendConfig={handleSaveBackendConfig}
                historyCount={history.length}
                onClearHistory={handleClearHistory}
                onResetAssistant={handleReset}
              />

              {/* History Sidebar Panel */}
              <HistoryPanel
                isOpen={showHistory}
                onClose={() => setShowHistory(false)}
                history={history}
                onReRunCommand={handleReRunHistoryCommand}
                onSelectHistoryEntry={handleSelectHistoryEntry}
                onClearHistory={handleClearHistory}
                isDark={isDark}
                colorTheme={colorTheme}
              />

              {/* Backend Connection Modal */}
              <BackendSettingsModal
                isOpen={showBackendModal}
                onClose={() => setShowBackendModal(false)}
                config={backendConfig}
                onSaveConfig={handleSaveBackendConfig}
                isDark={isDark}
                colorTheme={colorTheme}
              />

              {/* Canvas Visualizer Canvas Layer (Ribbon Wave, Particle Orb) */}
              <CanvasVisualizer
                mode={visualizerMode}
                state={state}
                colorTheme={colorTheme}
                isDark={isDark}
                compact={state !== "idle" && state !== "listening"}
                isPaused={showSettings || showBackendModal}
              />

              {/* Central Display & Animated State Cards with 60fps Hardware-Accelerated Smooth Scrolling */}
              <div
                id="central-display-area"
                className="relative z-20 flex-1 min-h-0 w-full max-w-4xl flex flex-col items-center px-4 py-3 sm:py-5 overflow-y-auto smooth-scroll-container bg-transparent"
              >
                <div className="w-full my-auto flex flex-col items-center justify-center py-2">
                  {/* State Pill Badge (Only for listening or interactive pickers) */}
                  {state === "listening" && (
                    <div className="mb-2 sm:mb-3">
                      <KineticStateBadge
                        stateText={liveTranscript ? "Live Transcribing" : "Listening to voice"}
                        colorTheme={colorTheme}
                        isDark={isDark}
                        pulse={true}
                      />
                    </div>
                  )}

                  {/* Main Central Spoken / Heading Text (Hidden during action execution to ensure single pill focus) */}
                  {state !== "action_card" && !(isActionIntent(activePrompt) && (state === "processing" || state === "working")) && (
                    <div className="text-center max-w-2xl mx-auto mb-4 sm:mb-6 px-4">
                      {state === "listening" && liveTranscript ? (
                        <div className="flex flex-col items-center">
                          <KineticHeading
                            text={liveTranscript}
                            isDark={isDark}
                            colorTheme={colorTheme}
                            animationStyle={textAnimationStyle}
                            className="text-xl sm:text-2xl md:text-3xl font-semibold tracking-tight leading-snug"
                          />
                        </div>
                      ) : state === "listening" ? (
                        <div className="flex flex-col items-center space-y-1">
                          <KineticHeading
                            text="Listening... speak now"
                            isDark={isDark}
                            colorTheme={colorTheme}
                            animationStyle={textAnimationStyle}
                            className="text-xl sm:text-2xl md:text-3xl font-medium tracking-tight leading-snug"
                          />
                          <p className="text-xs sm:text-sm opacity-60">
                            Your voice will transcribe seamlessly in real time
                          </p>
                        </div>
                      ) : (
                        <div
                          className={`group relative inline-flex flex-col items-center select-none max-h-[46vh] overflow-y-auto no-scrollbar px-2 ${
                            state === "idle" ? "cursor-pointer" : ""
                          }`}
                          onClick={state === "idle" ? handleShuffleGreeting : undefined}
                          title={state === "idle" ? "Click to cycle greeting phrase" : undefined}
                        >
                          <KineticHeading
                            text={state === "processing" ? (activePrompt || "Thinking...") : displayText}
                            isDark={isDark}
                            colorTheme={colorTheme}
                            animationStyle={textAnimationStyle}
                            className={`${
                              displayText.split(" ").length > 30
                                ? "text-sm sm:text-base md:text-lg leading-relaxed font-normal"
                                : displayText.split(" ").length > 14
                                ? "text-base sm:text-xl md:text-2xl leading-snug font-medium"
                                : "text-xl sm:text-2xl md:text-3xl font-medium tracking-tight leading-snug"
                            } group-hover:opacity-90 transition-opacity`}
                          />
                          {state === "idle" && (
                            <span className="opacity-0 group-hover:opacity-60 transition-opacity text-[10px] mt-1.5 flex items-center space-x-1 font-medium tracking-wide">
                              <Shuffle className="w-2.5 h-2.5" />
                              <span>Click to shuffle greeting</span>
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Interactive State Cards Section */}
                  <div className="w-full flex items-center justify-center">
                    <AnimatePresence mode="wait">
                      {/* 1. Action Card Confirmation (if user action requires checkboxes) */}
                      {state === "action_card" && assistantData && (
                        <ActionCard
                          key="action-card"
                          items={assistantData.actionCards}
                          onToggleItem={handleToggleActionItem}
                          onExecuteSingleItem={handleExecuteSingleAction}
                          onConfirm={handleConfirmActions}
                          onCancel={handleReset}
                          isDark={isDark}
                          colorTheme={colorTheme}
                        />
                      )}

                      {/* 2. Contact / Match Disambiguation Picker */}
                      {state === "contact_picker" && assistantData && (
                        <ContactPicker
                          key="contact-picker"
                          question={assistantData.disambiguationQuestion}
                          contacts={assistantData.contactMatches || []}
                          onSelectContact={handleSelectContact}
                          isDark={isDark}
                          colorTheme={colorTheme}
                        />
                      )}

                      {/* 3. Single Unified Gemini Action Pill (From Routing to Executed in ONE Continuous Pill) */}
                      {(state === "processing" || state === "working" || state === "completed") &&
                        isActionIntent(activePrompt) && (
                        <IntentBridgeHUD
                          key="intent-bridge-hud"
                          prompt={activePrompt}
                          isDark={isDark}
                          colorTheme={colorTheme}
                          isCompleted={state === "completed"}
                          statusText={
                            state === "completed"
                              ? (assistantData?.executionSummary?.headline || "Action Completed")
                              : state === "working"
                              ? "Executing Action..."
                              : undefined
                          }
                        />
                      )}
                    </AnimatePresence>
                  </div>
                </div>
              </div>

              {/* Bottom Interactive Voice & Text Bar with Chips */}
              <VoiceControls
                onProcessCommand={handleProcessCommand}
                isListening={isListening}
                onSetListening={handleSetListening}
                onTranscriptChange={setLiveTranscript}
                isDark={isDark}
                colorTheme={colorTheme}
                disabled={loading || state === "working"}
                backendConfig={backendConfig}
              />
            </main>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
