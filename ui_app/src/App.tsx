import React, { useState, useEffect, useCallback, useRef } from "react";
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
  AttachmentItem,
} from "./types";
import { CanvasVisualizer } from "./components/CanvasVisualizer";
import { TitleBar } from "./components/TitleBar";
import { ActionCard } from "./components/ActionCard";
import { ContactPicker } from "./components/ContactPicker";
import { VoiceControls } from "./components/VoiceControls";
import { HistoryPanel } from "./components/HistoryPanel";
import { BackendSettingsModal } from "./components/BackendSettingsModal";
import { SettingsPage } from "./components/SettingsPage";
import { KineticHeading, KineticStateBadge, TextAnimationStyle, normalizeAnimationStyle } from "./components/KineticText";
import { IntentBridgeHUD, isActionIntent } from "./components/IntentBridgeHUD";
import { COLOR_THEMES, GREETING_PRESETS } from "./data/presets";
import { speakText, sfx } from "./utils/audio";
import { processVoiceCommand, executeBackendAction } from "./services/assistantApi";
import { Sparkles, ChevronUp, Shuffle } from "lucide-react";

export default function App() {
  const [isDark, setIsDark] = useState<boolean>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_dark");
      if (saved !== null) return saved === "true";
    }
    return true;
  });

  const [soundEnabled, setSoundEnabled] = useState<boolean>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_sound");
      if (saved !== null) return saved === "true";
    }
    return true;
  });

  const [colorTheme, setColorTheme] = useState<ColorTheme>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_color_theme");
      if (saved && (COLOR_THEMES as any)[saved]) return saved as ColorTheme;
    }
    return "violet";
  });

  const [visualizerMode, setVisualizerMode] = useState<VisualizerMode>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_visualizer_mode");
      if (saved) return saved as VisualizerMode;
    }
    return "ribbon";
  });

  const [textAnimationStyle, setTextAnimationStyle] = useState<TextAnimationStyle>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_anim_style");
      if (saved) return normalizeAnimationStyle(saved);
    }
    return "silk_blur";
  });

  const [pluginMode, setPluginMode] = useState<PluginMode>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_plugin_mode");
      if (saved) return saved as PluginMode;
    }
    return "fullscreen";
  });

  const [isOpen, setIsOpen] = useState<boolean>(true);
  const [showHistory, setShowHistory] = useState<boolean>(false);
  const [showSettings, setShowSettings] = useState<boolean>(false);
  const [showBackendModal, setShowBackendModal] = useState<boolean>(false);

  // Greeting configuration
  const [greetingText, setGreetingText] = useState<string>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_greeting");
      if (saved) return saved;
    }
    return "What can I help you with ?";
  });

  const [autoCycleGreetings, setAutoCycleGreetings] = useState<boolean>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_autocycle_greeting");
      if (saved !== null) return saved === "true";
    }
    return true;
  });

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

  // Persistent localStorage synchronization
  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_dark", String(isDark)); } catch (e) {}
  }, [isDark]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_sound", String(soundEnabled)); } catch (e) {}
  }, [soundEnabled]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_color_theme", colorTheme); } catch (e) {}
  }, [colorTheme]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_visualizer_mode", visualizerMode); } catch (e) {}
  }, [visualizerMode]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_anim_style", textAnimationStyle); } catch (e) {}
  }, [textAnimationStyle]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_plugin_mode", pluginMode); } catch (e) {}
  }, [pluginMode]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_greeting", greetingText); } catch (e) {}
  }, [greetingText]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_autocycle_greeting", String(autoCycleGreetings)); } catch (e) {}
  }, [autoCycleGreetings]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_autocycle_interval", String(autoCycleInterval)); } catch (e) {}
  }, [autoCycleInterval]);

  // Persistent Backend configuration for plug-and-play connection
  const [backendConfig, setBackendConfig] = useState<BackendConfig>(() => {
    if (typeof window !== "undefined") {
      try {
        const saved = localStorage.getItem("assistant_backend_config");
        if (saved) {
          const parsed = JSON.parse(saved);
          if (parsed && typeof parsed === "object") {
            if (parsed.endpointUrl && parsed.endpointUrl.includes("127.0.0.1:5000")) {
              parsed.endpointUrl = parsed.endpointUrl.replace(/^https?:\/\/127\.0\.0\.1:5000/, "");
            }
            if (parsed.actionWebhookUrl && parsed.actionWebhookUrl.includes("127.0.0.1:5000")) {
              parsed.actionWebhookUrl = parsed.actionWebhookUrl.replace(/^https?:\/\/127\.0\.0\.1:5000/, "");
            }
            return parsed;
          }
        }
      } catch (e) {}
    }
    return {
      endpointUrl: "/api/assistant/process",
      actionWebhookUrl: "/api/action/execute",
      apiKey: "",
      customHeaders: "",
      protocol: "rest",
      autoSpeech: true,
      transcriptionEngine: "amigo-speech",
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
          isDark,
          theme: isDark ? "dark" : "light",
          soundEnabled,
          colorTheme,
          visualizerMode,
          textAnimationStyle,
          pluginMode,
          greetingText,
          autoCycleGreetings,
          autoCycleInterval,
          backendConfig: config,
          autoSpeech: config.autoSpeech,
          thinkingEnabled: config.thinkingEnabled,
        }),
      });
    } catch (e) {}
  };


  const isBackendCustom = Boolean(
    backendConfig.endpointUrl && backendConfig.endpointUrl !== "/api/assistant/process"
  );

  const [history, setHistory] = useState<HistoryEntry[]>([]);

  const cleanHistoryPrompt = (raw: string): string => {
    if (!raw) return "";
    const match = raw.match(/^\[Attached (?:Document|Image|File):\s*([^\]\n]+)\]/);
    if (match) {
      const filename = match[1].trim();
      const qMatch = raw.match(/User Question \/ Task:\s*(.+)$/s);
      if (qMatch && qMatch[1]) {
        const userQ = qMatch[1].trim();
        const lowerQ = userQ.toLowerCase();
        const lowerFn = filename.toLowerCase();
        if (
          lowerQ === `analyze ${lowerFn}` ||
          (lowerQ.includes(lowerFn) && (lowerQ.startsWith("analyze") || lowerQ.startsWith("summarize")))
        ) {
          return `Analyze: ${filename}`;
        }
        return `${userQ} (${filename})`;
      }
      return `Analyze: ${filename}`;
    }
    return raw;
  };

  // Fetch conversation history directly from Amigo memory backend on mount
  const fetchBackendHistory = async () => {
    try {
      const res = await fetch("/api/history");
      if (res.ok) {
        const memory = await res.json();
        if (memory && Array.isArray(memory.conversations)) {
          const formatted: HistoryEntry[] = memory.conversations.map((c: any, idx: number) => {
            const cleanUser = cleanHistoryPrompt(c.user || "");
            return {
              id: `hist-${idx}-${new Date(c.timestamp || Date.now()).getTime()}`,
              prompt: cleanUser,
              timestamp: new Date(c.timestamp || Date.now()).getTime(),
              status: "completed",
              response: {
                speechReply: c.assistant || "",
                displayTitle: cleanUser,
                intent: c.tool || "chat",
                requiresDisambiguation: false,
                actionCards: [],
                executionSummary: {
                  status: "completed",
                  headline: "Executed with Amigo",
                  details: c.assistant || "",
                },
              },
            };
          });
          setHistory(formatted.reverse());
        }
      }
    } catch (e) {}
  };

  // Fetch saved settings from Amigo backend on mount
  const fetchBackendSettings = async () => {

    try {
      const res = await fetch("/api/settings");
      if (res.ok) {
        const data = await res.json();
        const ui = data.ui_settings;
        if (ui && typeof ui === "object") {
          if (typeof ui.isDark === "boolean") setIsDark(ui.isDark);
          if (typeof ui.soundEnabled === "boolean") setSoundEnabled(ui.soundEnabled);
          if (ui.colorTheme && (COLOR_THEMES as any)[ui.colorTheme]) setColorTheme(ui.colorTheme);
          if (ui.visualizerMode) setVisualizerMode(ui.visualizerMode);
          if (ui.textAnimationStyle) setTextAnimationStyle(ui.textAnimationStyle);
          if (ui.pluginMode) setPluginMode(ui.pluginMode);
          if (ui.greetingText) setGreetingText(ui.greetingText);
          if (typeof ui.autoCycleGreetings === "boolean") setAutoCycleGreetings(ui.autoCycleGreetings);
          if (typeof ui.autoCycleInterval === "number") setAutoCycleInterval(ui.autoCycleInterval);
          if (ui.backendConfig && typeof ui.backendConfig === "object") {
            setBackendConfig((prev) => ({ ...prev, ...ui.backendConfig }));
          }
          if (typeof ui.thinkingEnabled === "boolean") {
            setBackendConfig((prev) => ({ ...prev, thinkingEnabled: ui.thinkingEnabled }));
          }
        }
      }
    } catch (e) {}
  };

  useEffect(() => {
    fetchBackendHistory();
    fetchBackendSettings();
  }, []);


  const [state, setState] = useState<AssistantState>("idle");
  const [activePrompt, setActivePrompt] = useState<string>("");
  const activePromptRef = useRef<string>("");
  const [displayText, setDisplayText] = useState<string>(greetingText);
  const [isListening, setIsListening] = useState<boolean>(false);
  const [liveTranscript, setLiveTranscript] = useState<string>("");

  const [assistantData, setAssistantData] = useState<AssistantResponse | null>(null);
  const [selectedContact, setSelectedContact] = useState<ContactItem | undefined>(undefined);
  const [loading, setLoading] = useState<boolean>(false);
  const loadingRef = useRef<boolean>(false);
  const [hudDismissed, setHudDismissed] = useState<boolean>(false);

  // Real-time bidirectional SSE sync with Amigo Python voice loop & server events
  useEffect(() => {
    let es: EventSource | null = null;
    let reconnectTimeout: any = null;

    const connectSSE = () => {
      try {
        es = new EventSource("/events");
        es.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            window.dispatchEvent(new CustomEvent("amigo_sse", { detail: data }));
            if (data.type === "state_change") {
              const s = data.state;
              if (s === "listening") {
                setIsListening(true);
                setState("listening");
              } else if (s === "idle") {
                setIsListening(false);
                if (!loadingRef.current) {
                  setState((current) => (current === "action_card" || current === "completed") ? current : "idle");
                }
              } else if (s === "speaking") {
                setState("completed");
              }
            } else if (data.type === "chat_message") {
              if (data.sender === "user") {
                setActivePrompt(data.text);
                activePromptRef.current = data.text;
                setDisplayText(data.text);
                setState("processing");
              } else if (data.sender === "assistant" || data.sender === "amigo") {
                setDisplayText(data.text);
                setState("completed");

                // Optimistically update History Drawer immediately
                const userPrompt = data.user_query || activePromptRef.current || "Voice Command";
                const cleanUser = cleanHistoryPrompt(userPrompt);
                const optimisticEntry: HistoryEntry = {
                  id: `hist-live-${Date.now()}`,
                  prompt: cleanUser,
                  timestamp: Date.now(),
                  status: "completed",
                  response: {
                    speechReply: data.text,
                    displayTitle: cleanUser,
                    intent: data.tool || "chat",
                    requiresDisambiguation: false,
                    actionCards: [],
                    executionSummary: {
                      status: "completed",
                      headline: "Executed with Amigo",
                      details: data.text,
                    },
                  },
                };
                setHistory((prev) => [optimisticEntry, ...prev.filter((p) => p.prompt !== cleanUser || Math.abs(p.timestamp - optimisticEntry.timestamp) > 5000)]);
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
          es = null;
          clearTimeout(reconnectTimeout);
          reconnectTimeout = setTimeout(connectSSE, 4000);
        };
      } catch (err) {}
    };

    connectSSE();
    return () => {
      clearTimeout(reconnectTimeout);
      es?.close();
      es = null;
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

  // Update greeting text only if currently in idle state and no active response displayed
  useEffect(() => {
    if (state === "idle") {
      if (autoCycleGreetings) {
        const activeText = GREETING_PRESETS[greetingIndex]?.text || GREETING_PRESETS[0].text;
        setGreetingText(activeText);
        setDisplayText((current) => (current === greetingText || GREETING_PRESETS.some((g) => g.text === current)) ? activeText : current);
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
    setHudDismissed(false);
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
  }, [greetingText]);

  // Keep AI response on stage until next interaction or user reset

  // Process user voice or typed command (with optional file attachment)
  const handleProcessCommand = async (prompt: string, attachment?: AttachmentItem) => {
    const effectivePrompt = prompt.trim() || (attachment ? `Analyze ${attachment.filename}` : "");
    if (!effectivePrompt) return;

    setHudDismissed(false);
    loadingRef.current = true;
    setActivePrompt(effectivePrompt);
    activePromptRef.current = effectivePrompt;
    setDisplayText(effectivePrompt);
    setState("processing");
    setLoading(true);

    try {
      const data = await processVoiceCommand(effectivePrompt, backendConfig, undefined, attachment);
      setAssistantData(data);
      setLoading(false);
      loadingRef.current = false;

      // Update center stage with the full real assistant response text
      const resultText = data.speechReply || data.executionSummary?.details || data.displayTitle || effectivePrompt;
      setDisplayText(resultText);

      // Append to command history
      const newEntry: HistoryEntry = {
        id: `hist-${Date.now()}`,
        timestamp: Date.now(),
        prompt: effectivePrompt,
        response: data,
      };
      setHistory((prev) => [
        newEntry,
        ...prev.filter((p) => p.prompt.toLowerCase() !== effectivePrompt.toLowerCase()),
      ]);

      const hasActionCards = Boolean(
        data.actionCards &&
        data.actionCards.length > 0 &&
        data.actionCards.some((c: any) => c.type !== "file")
      );

      if (data.requiresDisambiguation && data.contactMatches && data.contactMatches.length > 0) {
        setState("contact_picker");
        if (data.disambiguationQuestion) {
          setDisplayText(data.disambiguationQuestion);
        }
      } else if (hasActionCards) {
        setState("action_card");
      } else {
        setState("completed");
      }

    } catch (err) {
      console.warn("Assistant processing fallback:", err);
      setLoading(false);
      loadingRef.current = false;
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
    setHudDismissed(false);
    setActivePrompt(entry.prompt);
    const responseData = entry.response;
    setAssistantData(responseData);
    setSelectedContact(entry.selectedContact);
    const fullText = responseData.speechReply || responseData.executionSummary?.details || responseData.displayTitle || entry.prompt;
    setDisplayText(fullText);
    const hasActionCards = Boolean(
      responseData.actionCards &&
      responseData.actionCards.length > 0 &&
      responseData.actionCards.some((c: any) => c.type !== "file")
    );
    setState(hasActionCards ? "action_card" : responseData.requiresDisambiguation ? "contact_picker" : "completed");

    setShowHistory(false);
    if (responseData.speechReply && backendConfig.autoSpeech !== false && soundEnabled) {
      speakText(responseData.speechReply);
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
    setDisplayText("Working on this...");
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
      // Direct completion: restore the actual result text
      setTimeout(() => {
        sfx.playSuccess();
        const finalMsg =
          assistantData?.speechReply ||
          assistantData?.executionSummary?.details ||
          "Task completed successfully.";
        setDisplayText(finalMsg);
        setState("completed");
      }, 1200);
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
    <div className={`relative w-screen h-screen overflow-hidden ${colorTheme === "noir" ? "bg-black" : "bg-slate-950"} flex items-center justify-center`}>
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
            {colorTheme !== "noir" && (
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
            )}

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
              showSettings={showSettings}
              onToggleSettings={() => setShowSettings(!showSettings)}
              onOpenBackendSettings={() => setShowBackendModal(true)}
              isBackendCustom={isBackendCustom}
            />

            {/* Primary Workspace Area with Visualizer, Dynamic Content & History Sidebar */}
            <main className="relative flex-1 flex flex-col justify-between items-center z-10 w-full min-h-0 overflow-hidden">

              {/* Full Settings Page */}
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
                  {(state === "listening" || isListening) && (
                    <div className="mb-2 sm:mb-3">
                      <KineticStateBadge
                        stateText={liveTranscript ? "Live Transcribing" : "Listening to voice"}
                        colorTheme={colorTheme}
                        isDark={isDark}
                        pulse={true}
                      />
                    </div>
                  )}

                  {/* Main Central Spoken / Heading Text */}
                  {state !== "action_card" && (
                    <div className="text-center max-w-2xl mx-auto mb-4 sm:mb-6 px-4">
                      {(state === "listening" || isListening) && liveTranscript ? (
                        <div className="flex min-w-0 w-full flex-col items-center px-2">
                          <p className="w-full min-w-0 max-w-2xl break-words text-center text-xl font-semibold leading-snug text-slate-100 sm:text-2xl md:text-3xl">
                            {liveTranscript}
                          </p>
                        </div>
                      ) : (state === "listening" || isListening) ? (
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
                            text={
                              state === "processing" || state === "working"
                                ? (activePrompt || "Thinking...")
                                : (displayText || greetingText)
                            }
                            isDark={isDark}
                            colorTheme={colorTheme}
                            animationStyle={textAnimationStyle}
                            className={`${
                              (displayText || "").split(" ").length > 30
                                ? "text-sm sm:text-base md:text-lg leading-relaxed font-normal"
                                : (displayText || "").split(" ").length > 14
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
                          onRetry={() => handleReRunHistoryCommand(activePrompt)}
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
                      {state !== "action_card" &&
                        state !== "contact_picker" &&
                        (state === "processing" || state === "working" || state === "completed") &&
                        !hudDismissed &&
                        isActionIntent(activePrompt, assistantData?.intent) && (
                        <IntentBridgeHUD
                          key="intent-bridge-hud"
                          prompt={activePrompt}
                          isDark={isDark}
                          colorTheme={colorTheme}
                          intent={assistantData?.intent}
                          historyCount={history.length}
                          isCompleted={state === "completed"}
                          status={assistantData?.executionSummary?.status}
                          onDismiss={() => setHudDismissed(true)}
                          statusText={
                            state === "completed"
                              ? (assistantData?.executionSummary?.headline || "Completed")
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
