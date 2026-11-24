import React, { useState, useEffect, useCallback, useRef, lazy, Suspense } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  AssistantState,
  VisualizerMode,
  ColorTheme,
  ThinkingOrbStyle,
  AssistantResponse,
  ContactItem,
  PluginMode,
  HistoryEntry,
  BackendConfig,
  AttachmentItem,
  ViewMode,
} from "./types";
import { OrbVisualizer } from "./components/OrbVisualizer";
import { TitleBar } from "./components/TitleBar";
import { ContactPicker } from "./components/ContactPicker";
import { VoiceControls } from "./components/VoiceControls";
import { ChatWorkspace } from "./components/ChatWorkspace";
import { KineticHeading, KineticStateBadge, TextAnimationStyle, normalizeAnimationStyle } from "./components/KineticText";
import { IntentBridgeHUD, isActionIntent } from "./components/IntentBridgeHUD";
import { COLOR_THEMES, GREETING_PRESETS } from "./data/presets";
import { speakText, sfx } from "./utils/audio";
import {
  processVoiceCommand,
  executeBackendAction,
  fetchAssistantSettings,
  saveAssistantSettings,
  fetchAssistantHistory,
  clearAssistantHistory,
  stripReasoningTags,
} from "./services/assistantApi";
import { Sparkles, ChevronUp, Shuffle, Copy, Check, Volume2, X, ThumbsUp, ThumbsDown } from "lucide-react";
import { ErrorState } from "@/components/ui/error-state";
import { SparklesCore } from "./components/ui/sparkles-core";

// Lazy load heavy panels and modals for optimal bundle splitting
const HistoryPanel = lazy(() =>
  import("./components/HistoryPanel").then((mod) => ({ default: mod.HistoryPanel }))
);
const BackendSettingsModal = lazy(() =>
  import("./components/BackendSettingsModal").then((mod) => ({ default: mod.BackendSettingsModal }))
);
const SettingsPage = lazy(() =>
  import("./components/SettingsPage").then((mod) => ({ default: mod.SettingsPage }))
);
const GeneratedContentPanel = lazy(() =>
  import("./components/GeneratedContentPanel").then((mod) => ({ default: mod.GeneratedContentPanel }))
);

function cn(...classes: (string | undefined | null | false)[]) {
  return classes.filter(Boolean).join(" ");
}

interface SparklesBackgroundProps {
  className?: string;
  isDark?: boolean;
  children?: React.ReactNode;
}

const DARK_SPARKLES = ["#ffffff", "#38bdf8", "#818cf8", "#c084fc", "#67e8f9"];
const LIGHT_SPARKLES = ["#0284c7", "#6366f1", "#06b6d4", "#3b82f6"];

const SparklesBackground = React.memo(function SparklesBackground({
  className,
  isDark,
  children,
}: SparklesBackgroundProps) {
  return (
    <div
      className={cn(
        "relative min-h-screen w-full overflow-hidden bg-neutral-100 dark:bg-neutral-950",
        className
      )}
    >
      {/* Sparkles Core Main Background */}
      <div className="absolute inset-0 pointer-events-none z-0">
        <SparklesCore
          id="amigo-main-sparkles"
          background="transparent"
          minSize={0.6}
          maxSize={2.0}
          particleDensity={isDark ? 80 : 50}
          className="w-full h-full"
          particleColor={isDark ? "#ffffff" : "#0284c7"}
          particleColors={isDark ? DARK_SPARKLES : LIGHT_SPARKLES}
          speed={0.8}
          twinkle={true}
          twinkleSpeed={1.2}
          interactive={true}
          cursorMode="repulse"
          cursorRadius={130}
          clickPush={8}
        />
      </div>

      <div
        className="pointer-events-none absolute inset-0 bg-neutral-900/10 dark:bg-neutral-950/20"
      />

      {children}
    </div>
  );
});

function renderFormattedContent(text: string) {
  if (!text) return null;
  const blocks = text.split(/\n\s*\n/).filter((b) => b.trim());
  if (blocks.length <= 1) {
    const lines = text.split(/\n/).filter((l) => l.trim());
    if (lines.length > 1) {
      return (
        <div className="space-y-2">
          {lines.map((line, idx) => (
            <p key={idx} className="leading-relaxed">
              {line.trim()}
            </p>
          ))}
        </div>
      );
    }
    return <p className="leading-relaxed whitespace-pre-wrap">{text.trim()}</p>;
  }
  return (
    <div className="space-y-3">
      {blocks.map((block, idx) => (
        <p key={idx} className="leading-relaxed whitespace-pre-wrap">
          {block.trim()}
        </p>
      ))}
    </div>
  );
}

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
      if (saved && (COLOR_THEMES as any)[saved] && saved !== "violet") return saved as ColorTheme;
    }
    return "beams";
  });

  const [visualizerMode, setVisualizerMode] = useState<VisualizerMode>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_visualizer_mode");
      if (saved) return saved as VisualizerMode;
    }
    return "orb";
  });

  const [thinkingOrbStyle, setThinkingOrbStyle] = useState<ThinkingOrbStyle>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_thinking_orb_style");
      if (saved) return saved as ThinkingOrbStyle;
    }
    return "orbits";
  });

  const [webSearchOrbStyle, setWebSearchOrbStyle] = useState<ThinkingOrbStyle>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("windows11_voice_assistant_web_search_orb_style");
      if (saved) return saved as ThinkingOrbStyle;
    }
    return "globe";
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
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("amigo_view_mode");
      if (saved === "chat" || saved === "assistant") return saved as ViewMode;
    }
    return "assistant";
  });

  useEffect(() => {
    try {
      localStorage.setItem("amigo_view_mode", viewMode);
    } catch (e) { }
  }, [viewMode]);

  const handleSwitchViewMode = useCallback((newMode: ViewMode) => {
    setViewMode(newMode);
    if (newMode === "assistant") {
      // 1. Immediately cancel any speech synthesis / TTS playback
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
      // 2. Clear long chat response so the voice HUD returns to the clean 3D Orb and greeting
      setDisplayText("");
      setActivePrompt("");
      setState("idle");
      setLiveTranscript("");
      setHudActive(false);
      setHudDismissed(true);
    }
  }, []);

  const [userName, setUserName] = useState<string>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("amigo_user_name");
      if (saved) return saved;
    }
    return "";
  });

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
      } catch (e) { }
    }
    return 10;
  });
  const [greetingIndex, setGreetingIndex] = useState<number>(0);

  // Persistent localStorage & HTML root class synchronization for dark/light mode
  useEffect(() => {
    if (typeof document !== "undefined") {
      const root = document.documentElement;
      if (isDark) {
        root.classList.add("dark");
        root.style.colorScheme = "dark";
      } else {
        root.classList.remove("dark");
        root.style.colorScheme = "light";
      }
    }
    try { localStorage.setItem("windows11_voice_assistant_dark", String(isDark)); } catch (e) { }
  }, [isDark]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_sound", String(soundEnabled)); } catch (e) { }
  }, [soundEnabled]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_color_theme", colorTheme); } catch (e) { }
  }, [colorTheme]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_visualizer_mode", visualizerMode); } catch (e) { }
  }, [visualizerMode]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_thinking_orb_style", thinkingOrbStyle); } catch (e) { }
  }, [thinkingOrbStyle]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_web_search_orb_style", webSearchOrbStyle); } catch (e) { }
  }, [webSearchOrbStyle]);


  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_anim_style", textAnimationStyle); } catch (e) { }
  }, [textAnimationStyle]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_plugin_mode", pluginMode); } catch (e) { }
  }, [pluginMode]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_greeting", greetingText); } catch (e) { }
  }, [greetingText]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_autocycle_greeting", String(autoCycleGreetings)); } catch (e) { }
  }, [autoCycleGreetings]);

  useEffect(() => {
    try { localStorage.setItem("windows11_voice_assistant_autocycle_interval", String(autoCycleInterval)); } catch (e) { }
  }, [autoCycleInterval]);

  const overlayRef = useRef<HTMLIFrameElement | null>(null);
  const [overlayHot, setOverlayHot] = useState(false);

  useEffect(() => {
    const onMsg = (ev: MessageEvent) => {
      if (ev.data?.type === "amg_hot") setOverlayHot(Boolean(ev.data.hot));
    };
    const onMove = (e: PointerEvent) => {
      overlayRef.current?.contentWindow?.postMessage(
        { type: "amg_ptr", x: e.clientX, y: e.clientY },
        "*"
      );
    };
    window.addEventListener("message", onMsg);
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      window.removeEventListener("message", onMsg);
      window.removeEventListener("pointermove", onMove);
    };
  }, []);

  // Dynamic iOS / Mobile Virtual Viewport synchronization
  useEffect(() => {
    if (typeof window === "undefined") return;

    let rafId = 0;
    const handleViewportChange = () => {
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        const vv = window.visualViewport;
        const height = vv ? vv.height : window.innerHeight;
        document.documentElement.style.setProperty("--visual-viewport-height", `${height}px`);
        if (window.scrollY !== 0) {
          window.scrollTo(0, 0);
        }
      });
    };

    handleViewportChange();

    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", handleViewportChange);
      window.visualViewport.addEventListener("scroll", handleViewportChange);
    }
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("orientationchange", handleViewportChange);

    return () => {
      cancelAnimationFrame(rafId);
      if (window.visualViewport) {
        window.visualViewport.removeEventListener("resize", handleViewportChange);
        window.visualViewport.removeEventListener("scroll", handleViewportChange);
      }
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("orientationchange", handleViewportChange);
    };
  }, []);

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
      } catch (e) { }
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
      await saveAssistantSettings({
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
      });
    } catch (e) { }
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
      const memory = await fetchAssistantHistory();
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
        const reversed = formatted.reverse();
        setHistory(reversed);

        // Restore latest conversation onto stage ONCE on initial mount only
        if (!initialRestoreDoneRef.current && reversed.length > 0 && !activePromptRef.current) {
          initialRestoreDoneRef.current = true;
          const latest = reversed[0];
          const reply = latest.response.speechReply || latest.response.executionSummary?.details;
          if (reply) {
            setDisplayText((current) => {
              const isGreeting = GREETING_PRESETS.some((g) => g.text === current) || current === greetingText;
              if (isGreeting) {
                setActivePrompt(latest.prompt);
                activePromptRef.current = latest.prompt;
                setAssistantData(latest.response);
                setState("completed");
                return reply;
              }
              return current;
            });
          }
        }
      }
    } catch (e) { }
  };

  // Fetch saved settings from Amigo backend on mount
  const fetchBackendSettings = async () => {
    try {
      const data = await fetchAssistantSettings();
      const ui = data?.ui_settings;
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
      const name = data?.user_name || data?.user_profile?.identity?.name;
      if (name && typeof name === "string") {
        setUserName(name);
        try { localStorage.setItem("amigo_user_name", name); } catch (e) { }
      }
    } catch (e) { }
  };

  useEffect(() => {
    fetchBackendHistory();
    fetchBackendSettings();
  }, []);


  const initialRestoreDoneRef = useRef<boolean>(false);
  const [state, setState] = useState<AssistantState>("idle");
  const [activePrompt, setActivePrompt] = useState<string>("");
  const activePromptRef = useRef<string>("");
  const [displayText, setDisplayText] = useState<string>(greetingText);
  const [isListening, setIsListening] = useState<boolean>(false);
  const [liveTranscript, setLiveTranscript] = useState<string>("");
  const [turnId, setTurnId] = useState<string>(() => `turn-${Date.now()}`);
  const [hasResponseOverflow, setHasResponseOverflow] = useState<boolean>(false);
  const responseContainerRef = useRef<HTMLDivElement>(null);

  const [assistantData, setAssistantData] = useState<AssistantResponse | null>(null);
  const [selectedContact, setSelectedContact] = useState<ContactItem | undefined>(undefined);
  const [loading, setLoading] = useState<boolean>(false);
  const loadingRef = useRef<boolean>(false);
  const [hudDismissed, setHudDismissed] = useState<boolean>(false);
  const [hudActive, setHudActive] = useState<boolean>(false);
  const [liveIntent, setLiveIntent] = useState<string | null>(null);
  const [liveParams, setLiveParams] = useState<Record<string, any> | null>(null);

  // Generated content panel state
  const [generatedContent, setGeneratedContent] = useState<{
    content: string;
    contentType: string;
    topic: string;
  } | null>(null);

  // Response card toolbar state and actions
  const [copiedResponse, setCopiedResponse] = useState<boolean>(false);
  const handleCopyResponse = (text: string) => {
    try {
      navigator.clipboard.writeText(text);
      setCopiedResponse(true);
      sfx.playClick();
      setTimeout(() => setCopiedResponse(false), 2000);
    } catch (e) {
      console.warn("Clipboard copy failed:", e);
    }
  };

  // Reinforcement feedback states (Roamies v0.1.46 "Good or Wrong" learning loop)
  const [feedbackState, setFeedbackState] = useState<"idle" | "good" | "wrong_input" | "wrong_submitted">("idle");
  const [correctionInput, setCorrectionInput] = useState<string>("");
  const [isHoveringResponse, setIsHoveringResponse] = useState<boolean>(false);

  // Reset feedback state on new turn
  useEffect(() => {
    setFeedbackState("idle");
    setCorrectionInput("");
  }, [turnId]);

  const handleFeedback = async (type: "good" | "wrong", correctionText?: string) => {
    try {
      if (type === "good") {
        sfx.playSuccess();
        setFeedbackState("good");
        setTimeout(() => setFeedbackState("idle"), 2500);
      } else {
        sfx.playSuccess();
        setFeedbackState("wrong_submitted");
        setTimeout(() => setFeedbackState("idle"), 3000);
      }

      await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type,
          query: activePrompt || "User prompt",
          response: displayText,
          correction: correctionText || "",
        }),
      });
    } catch (e) {
      console.warn("Feedback post error:", e);
    }
  };

  // Error state for assistant errors across Amigo
  const [errorState, setErrorState] = useState<{
    title: string;
    detail: string;
    failedPrompt?: string;
    failedAttachment?: AttachmentItem;
  } | null>(null);
  const [isRetrying, setIsRetrying] = useState<boolean>(false);

  const handleDismissResponse = () => {
    sfx.playClick();
    setState("idle");
    setActivePrompt("");
    activePromptRef.current = "";
    setDisplayText(greetingText);
    setErrorState(null);
    setIsRetrying(false);
  };

  // Hard reset scroll position and dynamically detect overflow for smooth bottom fade
  useEffect(() => {
    const el = responseContainerRef.current;
    if (el) {
      el.scrollTop = 0;
      const checkOverflow = () => {
        setHasResponseOverflow(el.scrollHeight > el.clientHeight + 16);
      };
      checkOverflow();
      const ro = new ResizeObserver(checkOverflow);
      ro.observe(el);
      return () => ro.disconnect();
    }
    const centralArea = document.getElementById("central-display-area");
    if (centralArea) {
      centralArea.scrollTop = 0;
    }
  }, [displayText, turnId]);

  // Real-time bidirectional SSE sync with Amigo Python voice loop & server events
  useEffect(() => {
    let es: EventSource | null = null;
    let reconnectTimeout: any = null;

    const connectSSE = () => {
      try {
        es = new EventSource("/events");
        es.onopen = () => {
          window.dispatchEvent(new CustomEvent("amigo_backend_status", { detail: { online: true } }));
        };
        es.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            window.dispatchEvent(new CustomEvent("amigo_sse", { detail: data }));
            if (data.type === "state_change") {
              const s = data.state;
              if (s === "listening") {
                // Only update assistant visual state (3D Orb & HUD)
                // Do NOT set isListening to true here, as that starts the browser's MediaRecorder
                // while the backend hotkey is already listening, causing duplicate command execution!
                setTurnId(`turn-${Date.now()}`);
                setLiveTranscript("");
                setAssistantData(null);
                setState("listening");
                const centralArea = document.getElementById("central-display-area");
                if (centralArea) {
                  centralArea.scrollTop = 0;
                }
              } else if (s === "idle") {
                setIsListening(false);
                if (!loadingRef.current) {
                  setState((current) => {
                    if (current === "action_card" || current === "completed" || current === "generated_content" || current === "processing" || current === "working") {
                      return current;
                    }
                    if (activePromptRef.current) {
                      return "completed";
                    }
                    return "idle";
                  });
                }
              } else if (s === "speaking") {
                setState("completed");
              }
            } else if (data.type === "chat_message") {
              if (data.sender === "user") {
                setTurnId(`turn-${Date.now()}`);
                setLiveTranscript("");
                setAssistantData(null);
                setActivePrompt(data.text);
                activePromptRef.current = data.text;
                setDisplayText(data.text);
                setState("processing");
                setHudDismissed(false);
                setLiveIntent(null);
                setLiveParams(null);
                setHudActive(isActionIntent(data.text));
                const centralArea = document.getElementById("central-display-area");
                if (centralArea) {
                  centralArea.scrollTop = 0;
                }
              } else if (data.sender === "assistant" || data.sender === "amigo") {
                const userPrompt = data.user_query || activePromptRef.current || "Voice Command";
                const cleanUser = cleanHistoryPrompt(userPrompt);
                const cleanReply = stripReasoningTags(data.text || "");
                setActivePrompt(cleanUser);
                activePromptRef.current = cleanUser;
                setDisplayText(cleanReply);
                setAssistantData({
                  speechReply: cleanReply,
                  displayTitle: cleanUser,
                  intent: data.tool || "chat",
                  requiresDisambiguation: false,
                  actionCards: [],
                  executionSummary: {
                    status: "completed",
                    headline: "Executed with Amigo",
                    details: cleanReply,
                  },
                });
                setState((current) => {
                  if (current === "contact_picker" || current === "generated_content") {
                    return current;
                  }
                  return "completed";
                });

                // Optimistically update History Drawer immediately
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
              const detected = data.intent || data.tool;
              if (detected) {
                const cleanDetected = String(detected).toLowerCase();
                setLiveIntent(cleanDetected);
                if (isActionIntent("", cleanDetected)) {
                  setHudActive(true);
                  setHudDismissed(false);
                }
              }
              const p = data.params || {};
              setLiveParams({
                ...p,
                label: data.label || p.label,
                detail: data.detail || p.detail,
              });
              setState("working");
            } else if (data.type === "proactive_notification") {
              const msg = data.message || data.text;
              if (msg) {
                setDisplayText(msg);
                setState((current) => {
                  if (current === "action_card" || current === "contact_picker" || current === "generated_content") {
                    return current; // Preserve interactive UI states
                  }
                  return "completed";
                });
                const entry: HistoryEntry = {
                  id: `proactive-${Date.now()}`,
                  prompt: `Proactive Alert (${data.level || "Info"})`,
                  timestamp: Date.now(),
                  status: "completed",
                  response: {
                    speechReply: msg,
                    displayTitle: `Proactive Alert (${data.level || "Info"})`,
                    intent: "proactive",
                    requiresDisambiguation: false,
                    actionCards: [],
                    executionSummary: {
                      status: "completed",
                      headline: "Amigo Proactive Suggestion",
                      details: msg,
                    },
                  },
                };
                setHistory((prev) => [entry, ...prev]);
                fetchBackendHistory();
              }
            } else if (data.type === "history_cleared") {
              setHistory([]);
            } else if (data.type === "open_settings" || data.type === "show_settings") {
              setShowSettings(true);
            } else if (data.type === "close_settings" || data.type === "hide_settings") {
              setShowSettings(false);
            } else if (data.type === "open_history" || data.type === "show_history") {
              setShowHistory(true);
            } else if (data.type === "close_history" || data.type === "hide_history") {
              setShowHistory(false);
            } else if (data.type === "error" || (data.type === "chat_message" && data.error)) {
              const errMsg = data.error || data.message || data.text || "An unexpected error occurred.";
              setErrorState({
                title: data.headline || "Amigo Error",
                detail: String(errMsg),
                failedPrompt: activePromptRef.current || undefined,
              });
            }
          } catch (e) { }
        };
        es.onerror = () => {
          window.dispatchEvent(new CustomEvent("amigo_backend_status", { detail: { online: false } }));
          es?.close();
          es = null;
          clearTimeout(reconnectTimeout);
          reconnectTimeout = setTimeout(connectSSE, 4000);
        };
      } catch (err) { }
    };

    connectSSE();
    return () => {
      clearTimeout(reconnectTimeout);
      es?.close();
      es = null;
    };
  }, []);

  // Dynamic auto-cycling of greeting phrases when idle and no active response is displayed
  useEffect(() => {
    if (!autoCycleGreetings || state !== "idle" || activePrompt) return;

    const interval = setInterval(() => {
      setGreetingIndex((prevIndex) => (prevIndex + 1) % GREETING_PRESETS.length);
    }, autoCycleInterval * 1000);

    return () => clearInterval(interval);
  }, [autoCycleGreetings, state, autoCycleInterval, activePrompt]);

  // Update greeting text only if currently in idle state and no active prompt/response displayed
  useEffect(() => {
    if (state === "idle" && autoCycleGreetings && !activePrompt && !isListening) {
      const activeText = GREETING_PRESETS[greetingIndex]?.text || GREETING_PRESETS[0].text;
      setGreetingText(activeText);
      setDisplayText((current) => (GREETING_PRESETS.some((g) => g.text === current) ? activeText : current));
    }
  }, [greetingIndex, autoCycleGreetings, state, activePrompt, isListening]);

  // Quick cycle to next greeting phrase on click
  const handleShuffleGreeting = () => {
    sfx.playClick();
    const nextIndex = (greetingIndex + 1) % GREETING_PRESETS.length;
    setGreetingIndex(nextIndex);
    const nextText = GREETING_PRESETS[nextIndex]?.text || GREETING_PRESETS[0].text;
    setGreetingText(nextText);
    setDisplayText(nextText);
    try {
      localStorage.setItem("windows11_voice_assistant_greeting", nextText);
    } catch (_) {}
  };

  // Set listening state with synchronized assistant state
  const handleSetListening = (listening: boolean) => {
    setIsListening(listening);
    if (listening) {
      setTurnId(`turn-${Date.now()}`);
      setLiveTranscript("");
      setAssistantData(null);
      setState("listening");
      const centralArea = document.getElementById("central-display-area");
      if (centralArea) {
        centralArea.scrollTop = 0;
      }
    } else if (state === "listening" && !loading) {
      setState((current) => {
        if (current === "listening") {
          return activePromptRef.current ? "completed" : "idle";
        }
        return current;
      });
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
    setTurnId(`turn-${Date.now()}`);
    setState("idle");
    setActivePrompt("");
    setLiveTranscript("");
    setDisplayText(greetingText);
    setAssistantData(null);
    setSelectedContact(undefined);
    setIsListening(false);
    setLoading(false);
    setHudDismissed(true);
    setHudActive(false);
    setLiveIntent(null);
    setLiveParams(null);
    const centralArea = document.getElementById("central-display-area");
    if (centralArea) {
      centralArea.scrollTop = 0;
    }
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
  }, [greetingText]);

  // Keep AI response on stage until next interaction or user reset

  // Process user voice or typed command (with optional file attachment)
  const handleProcessCommand = async (prompt: string, attachment?: AttachmentItem) => {
    const effectivePrompt = prompt.trim() || (attachment ? `Analyze ${attachment.filename}` : "");
    if (!effectivePrompt) return;

    setTurnId(`turn-${Date.now()}`);
    setLiveTranscript("");
    setAssistantData(null);
    setErrorState(null);
    setIsRetrying(false);
    setHudDismissed(false);
    setLiveIntent(null);
    setLiveParams(null);
    setHudActive(isActionIntent(effectivePrompt));
    loadingRef.current = true;
    setActivePrompt(effectivePrompt);
    activePromptRef.current = effectivePrompt;
    setDisplayText(effectivePrompt);
    setState("processing");
    setLoading(true);

    const centralArea = document.getElementById("central-display-area");
    if (centralArea) {
      centralArea.scrollTop = 0;
    }

    try {
      const data = await processVoiceCommand(effectivePrompt, backendConfig, undefined, attachment);
      setAssistantData(data);
      setLoading(false);
      loadingRef.current = false;

      // Check for errors returned by backend logic or execution
      const isErrorResult =
        data.intent === "error" ||
        data.executionSummary?.status === "error" ||
        data.executionSummary?.status === "failed" ||
        Boolean(data.metadata?.error);

      if (isErrorResult) {
        const errorHeadline =
          data.executionSummary?.headline ||
          (data.intent === "error" ? "Execution Error" : "Command Error");
        const errorDetail =
          data.metadata?.error ||
          data.executionSummary?.details ||
          data.speechReply ||
          "An error occurred while executing this command.";
        setErrorState({
          title: errorHeadline,
          detail: String(errorDetail),
          failedPrompt: effectivePrompt,
          failedAttachment: attachment,
        });
        setHudActive(false);
      } else {
        setErrorState(null);
        const finalIntent = data.intent || liveIntent;
        if (finalIntent && isActionIntent(effectivePrompt, finalIntent)) {
          setHudActive(true);
        } else if (finalIntent && !isActionIntent(effectivePrompt, finalIntent)) {
          setHudActive(false);
        }
      }

      // Update center stage with the full real assistant response text
      const resultText = data.speechReply || data.executionSummary?.details || data.displayTitle || effectivePrompt;
      setDisplayText(stripReasoningTags(resultText));

      // Append to command history
      const newEntry: HistoryEntry = {
        id: `hist-${Date.now()}`,
        timestamp: Date.now(),
        prompt: effectivePrompt,
        status: isErrorResult ? "error" : "completed",
        response: data,
      };
      setHistory((prev) => [
        newEntry,
        ...prev.filter((p) => p.prompt.toLowerCase() !== effectivePrompt.toLowerCase()),
      ]);

      // Check for generated content panel
      if (data.showGeneratedPanel && data.generatedContent) {
        setGeneratedContent({
          content: data.generatedContent,
          contentType: data.contentType || "document",
          topic: data.contentTopic || effectivePrompt,
        });
        setState("generated_content");
      } else if (data.requiresDisambiguation && data.contactMatches && data.contactMatches.length > 0) {
        setState("contact_picker");
        if (data.disambiguationQuestion) {
          setDisplayText(data.disambiguationQuestion);
        }
      } else {
        setState("completed");
      }

    } catch (err: any) {
      console.warn("Assistant processing fallback:", err);
      setLoading(false);
      loadingRef.current = false;
      setIsRetrying(false);
      setState("completed");

      const errDetail =
        err?.message ||
        (typeof err === "string" ? err : "Failed to connect to Amigo backend. Check if the server is running.");
      setErrorState({
        title: "Connection / Processing Error",
        detail: errDetail,
        failedPrompt: effectivePrompt,
        failedAttachment: attachment,
      });

      const errEntry: HistoryEntry = {
        id: `hist-err-${Date.now()}`,
        timestamp: Date.now(),
        prompt: effectivePrompt,
        status: "error",
        response: {
          speechReply: errDetail,
          displayTitle: "Processing Failed",
          intent: "error",
          requiresDisambiguation: false,
          actionCards: [],
          metadata: { error: errDetail },
          executionSummary: {
            status: "error",
            headline: "Connection / Processing Error",
            details: errDetail,
          },
        },
      };
      setHistory((prev) => [
        errEntry,
        ...prev.filter((p) => p.prompt.toLowerCase() !== effectivePrompt.toLowerCase()),
      ]);
    }
  };

  const handleRetryError = useCallback(async () => {
    if (!errorState) return;
    const promptToRetry = errorState.failedPrompt || "";
    const attachmentToRetry = errorState.failedAttachment;
    setIsRetrying(true);
    sfx.playClick?.();
    try {
      await handleProcessCommand(promptToRetry, attachmentToRetry);
    } finally {
      setIsRetrying(false);
    }
  }, [errorState, backendConfig]);

  const handleCloseHistory = useCallback(() => {
    setShowHistory(false);
  }, []);

  // Re-run previous voice command from history
  const handleReRunHistoryCommand = useCallback((prompt: string) => {
    setShowHistory(false);
    handleProcessCommand(prompt);
  }, [backendConfig]);

  // Inspect previous result from history
  const handleSelectHistoryEntry = useCallback((entry: HistoryEntry) => {
    setHudDismissed(false);
    setActivePrompt(entry.prompt);
    const responseData = entry.response;
    setAssistantData(responseData);
    setSelectedContact(entry.selectedContact);
    const fullText = responseData.speechReply || responseData.executionSummary?.details || responseData.displayTitle || entry.prompt;
    setDisplayText(fullText);
    // Check for generated content in history
    if (responseData.showGeneratedPanel && responseData.generatedContent) {
      setGeneratedContent({
        content: responseData.generatedContent,
        contentType: responseData.contentType || "document",
        topic: responseData.contentTopic || entry.prompt,
      });
      setState("generated_content");
    } else {
      setState(responseData.requiresDisambiguation ? "contact_picker" : "completed");
    }

    setShowHistory(false);
    if (responseData.speechReply && backendConfig.autoSpeech !== false && soundEnabled) {
      speakText(responseData.speechReply);
    }
  }, [backendConfig.autoSpeech, soundEnabled]);

  // Generated content panel handlers
  const handleGeneratedContentInsert = async (content: string) => {
    // Call backend to insert content via clipboard
    try {
      await executeBackendAction({
        id: "insert_content",
        type: "general",
        title: "Insert Content",
        subtitle: "Paste generated content",
        selected: true,
        payload: { content },
        originalPrompt: `Insert generated content: ${content.slice(0, 100)}`,
      }, backendConfig);
      setDisplayText("Content inserted successfully!");
      setState("completed");
      setGeneratedContent(null);
    } catch (err) {
      console.error("Failed to insert content:", err);
      setDisplayText("Failed to insert content");
    }
  };

  const handleGeneratedContentCopy = (content: string) => {
    navigator.clipboard.writeText(content).then(() => {
      setDisplayText("Copied to clipboard!");
    }).catch(() => {
      setDisplayText("Failed to copy");
    });
  };

  const handleGeneratedContentRegenerate = async () => {
    if (!generatedContent) return;
    setDisplayText("Regenerating...");
    setState("working");
    try {
      await processVoiceCommand(`Regenerate ${generatedContent.contentType}: ${generatedContent.topic}`, backendConfig);
    } catch (err) {
      console.error("Failed to regenerate:", err);
    }
  };

  const handleGeneratedContentSave = async (content: string) => {
    // Save to a file or memory
    try {
      const blob = new Blob([content], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${generatedContent?.contentType || "content"}-${Date.now()}.txt`;
      a.click();
      URL.revokeObjectURL(url);
      setDisplayText("Content saved to file!");
    } catch (err) {
      console.error("Failed to save:", err);
      setDisplayText("Failed to save content");
    }
  };

  const handleGeneratedContentClose = () => {
    setGeneratedContent(null);
    setState("completed");
  };

  // Clear all command history across UI and Amigo memory backend
  const handleClearHistory = useCallback(async () => {
    sfx.playClick();
    setHistory([]);
    try {
      localStorage.removeItem("windows11_voice_assistant_history");
      await clearAssistantHistory();
    } catch (e) { }
  }, []);

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

  const activeThemeObj = COLOR_THEMES[colorTheme] || COLOR_THEMES.beams || COLOR_THEMES.violet;

  // Plugin container layout styling based on pluginMode
  const getPluginContainerClasses = () => {
    if (!isOpen) return "hidden";

    switch (pluginMode) {
      case "docked_right":
        return "fixed top-0 right-0 h-screen w-full sm:w-[460px] shadow-2xl z-50 border-l border-black/10 dark:border-white/10 bg-white/70 dark:bg-black/40 backdrop-blur-2xl";
      case "docked_bottom":
        return "fixed bottom-0 left-1/2 -translate-x-1/2 w-full max-w-4xl h-[560px] rounded-t-3xl shadow-2xl z-50 border-t border-x border-black/10 dark:border-white/10 bg-white/70 dark:bg-black/40 backdrop-blur-2xl";
      case "floating":
        return "fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[94vw] max-w-4xl h-[86vh] max-h-[820px] rounded-3xl shadow-2xl z-50 border border-black/10 dark:border-white/15 bg-white/75 dark:bg-black/45 backdrop-blur-2xl overflow-hidden";
      case "fullscreen":
      default:
        return "fixed inset-0 w-full h-[var(--visual-viewport-height,100dvh)] z-50";
    }
  };

  return (
    <SparklesBackground isDark={isDark} className="relative w-full h-[var(--visual-viewport-height,100dvh)] min-h-0 flex items-center justify-center">
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
            transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
            style={{ willChange: "transform, opacity", transform: "translate3d(0, 0, 0)" }}
            className={`flex flex-col transition-colors duration-500 bg-transparent ${isDark ? "text-white" : "text-slate-900"
              } ${getPluginContainerClasses()}`}
          >
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
              viewMode={viewMode}
              onToggleViewMode={() => handleSwitchViewMode(viewMode === "chat" ? "assistant" : "chat")}
              onChangeViewMode={handleSwitchViewMode}
            />

            {/* Primary Workspace Area with Visualizer, Dynamic Content & History Sidebar */}
            <main className="relative flex-1 flex flex-col justify-between items-center z-10 w-full min-h-0 overflow-hidden">

              {/* Full Settings Page */}
              <Suspense fallback={null}>
                <SettingsPage
                  isOpen={showSettings}
                  onClose={() => setShowSettings(false)}
                  isDark={isDark}
                  onToggleTheme={() => setIsDark(!isDark)}
                  colorTheme={colorTheme}
                  onChangeColorTheme={setColorTheme}
                  visualizerMode={visualizerMode}
                  onChangeVisualizerMode={setVisualizerMode}
                  thinkingOrbStyle={thinkingOrbStyle}
                  onChangeThinkingOrbStyle={setThinkingOrbStyle}
                  webSearchOrbStyle={webSearchOrbStyle}
                  onChangeWebSearchOrbStyle={setWebSearchOrbStyle}
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
              </Suspense>

              {/* History Sidebar Panel */}
              <Suspense fallback={null}>
                <HistoryPanel
                  isOpen={showHistory}
                  onClose={handleCloseHistory}
                  history={history}
                  onReRunCommand={handleReRunHistoryCommand}
                  onSelectHistoryEntry={handleSelectHistoryEntry}
                  onClearHistory={handleClearHistory}
                  isDark={isDark}
                  colorTheme={colorTheme}
                />
              </Suspense>

              {/* Backend Connection Modal */}
              <Suspense fallback={null}>
                <BackendSettingsModal
                  isOpen={showBackendModal}
                  onClose={() => setShowBackendModal(false)}
                  config={backendConfig}
                  onSaveConfig={handleSaveBackendConfig}
                  isDark={isDark}
                  colorTheme={colorTheme}
                />
              </Suspense>

              {/* View Switcher: Assistant Mode (3D Orb & Voice) vs Chat Mode (ChatGPT Layout with Graphs & Tables) */}
              <AnimatePresence mode="wait">
                {viewMode === "assistant" ? (
                  <motion.div
                    key="assistant-view"
                    initial={{ opacity: 0, scale: 0.98, filter: "blur(4px)" }}
                    animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
                    exit={{ opacity: 0, scale: 0.98, filter: "blur(4px)" }}
                    transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
                    className="relative flex-1 w-full min-h-0 flex flex-col justify-between items-center overflow-hidden"
                  >
                    {/* State-driven Thinking Orb & Central Display layer */}
                    {(() => {
                      const isCompact = state === "contact_picker";
                      return (
                        <div className="relative flex-1 w-full min-h-0 flex flex-col items-center justify-start overflow-hidden">
                          {/* Central Display Area with Orb above & Spoken / Greeting text cleanly below */}
                          <div
                            id="central-display-area"
                            className="relative z-20 flex-1 min-h-0 w-full max-w-4xl flex flex-col items-center justify-start px-4 pt-8 sm:pt-12 md:pt-14 pb-8 overflow-y-auto overflow-x-hidden smooth-scroll-container bg-transparent transition-opacity duration-200"
                          >
                            <div className="w-full flex flex-col items-center justify-start transition-opacity duration-200 max-w-2xl overflow-visible">
                              {/* 3D Orb Avatar (Positioned above text, never covered) */}
                              <div
                                className={`relative flex items-center justify-center transition-all duration-500 ease-out select-none overflow-visible ${isCompact ? "h-0 opacity-0 overflow-hidden mb-0" : "h-[15rem] sm:h-[16rem] shrink-0 mb-4 sm:mb-6"
                                  } ${state === "idle" ? "cursor-pointer active:scale-95 transition-transform" : ""}`}
                                onClick={state === "idle" ? handleShuffleGreeting : undefined}
                                title={state === "idle" ? "Click to change greeting phrase" : undefined}
                              >
                                <OrbVisualizer
                                  mode={visualizerMode}
                                  state={state}
                                  colorTheme={colorTheme}
                                  isDark={isDark}
                                  compact={isCompact}
                                  isPaused={showBackendModal}
                                  thinkingOrbStyle={thinkingOrbStyle}
                                  webSearchOrbStyle={webSearchOrbStyle}
                                  activeIntent={liveIntent || assistantData?.intent}
                                  activePrompt={activePrompt}
                                />
                              </div>

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



                              {/* Main Central Spoken / Heading Text (for idle, listening, processing, working, completed) */}
                              {!isCompact && (
                                <div className="w-full max-w-2xl sm:max-w-3xl md:max-w-4xl mx-auto px-4 drop-shadow-[0_4px_24px_rgba(0,0,0,0.85)] pointer-events-auto">
                                  {(state === "listening" || isListening) && liveTranscript ? (
                                    <div className="flex min-w-0 w-full flex-col items-center px-2 text-center">
                                      <p className="w-full min-w-0 max-w-2xl break-words text-center text-xl font-semibold leading-snug text-slate-100 sm:text-2xl md:text-3xl whitespace-pre-wrap">
                                        {liveTranscript}
                                      </p>
                                    </div>
                                  ) : (state === "listening" || isListening) ? (
                                    <div className="flex flex-col items-center space-y-1 text-center">
                                      <KineticHeading
                                        key={`${turnId}-listening`}
                                        turnId={turnId}
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
                                  ) : (() => {
                                    const isGreeting = GREETING_PRESETS.some((g) => g.text.toLowerCase() === (displayText || "").toLowerCase()) || !displayText;
                                    const isCompletedResponse = Boolean(displayText && !isGreeting && state === "completed");

                                    if (isCompletedResponse) {
                                      const isShortDirect = (() => {
                                        if (!displayText) return true;
                                        const raw = displayText.trim();
                                        if (raw.length > 240) return false;
                                        const hasTable = /(?:^|\n)\s*\|?\s*[:\-]{2,}\s*\|\s*[:\-]{2,}[\s\-:|]*(?:\n|$)/.test(raw);
                                        if (hasTable) return false;
                                        if (/^[•\-*]\s+|\b^\d+\.\s+/m.test(raw)) return false;
                                        return true;
                                      })();

                                      return (
                                        <div
                                          onMouseEnter={() => setIsHoveringResponse(true)}
                                          onMouseLeave={() => setIsHoveringResponse(false)}
                                          className={`w-full ${isShortDirect ? "max-w-[700px] text-center" : "max-w-[680px] text-left"} mx-auto`}
                                        >
                                          <div
                                            ref={responseContainerRef}
                                            className={`relative flex flex-col ${isShortDirect ? "items-center justify-start text-center" : "items-start justify-start text-left"} w-full select-text max-h-[46vh] overflow-y-auto no-scrollbar`}
                                            style={
                                              !isShortDirect && hasResponseOverflow
                                                ? {
                                                    maskImage:
                                                      "linear-gradient(to bottom, black calc(100% - 24px), transparent 100%)",
                                                    WebkitMaskImage:
                                                      "linear-gradient(to bottom, black calc(100% - 24px), transparent 100%)",
                                                  }
                                                : undefined
                                            }
                                          >
                                            <KineticHeading
                                              key={`${turnId}-completed`}
                                              turnId={turnId}
                                              text={displayText}
                                              isDark={isDark}
                                              colorTheme={colorTheme}
                                              animationStyle={textAnimationStyle}
                                              isHero={isShortDirect}
                                            />
                                          </div>

                                          {/* Clean Action Icons & Roamies Feedback Loop: Copy, Listen, Good/Wrong reinforcement, Dismiss */}
                                          <div className={`flex flex-col ${isShortDirect ? "items-center" : "items-start"} mt-4 select-none`}>
                                            <div className="flex items-center gap-2 opacity-50 hover:opacity-100 transition-opacity">
                                              <button
                                                type="button"
                                                onClick={() => handleCopyResponse(displayText)}
                                                className="w-8 h-8 rounded-full flex items-center justify-center text-slate-400 hover:text-[#f8fafc] hover:bg-white/[0.08] transition-colors"
                                                title={copiedResponse ? "Copied" : "Copy text"}
                                              >
                                                {copiedResponse ? <Check className="w-[15px] h-[15px] text-emerald-400" /> : <Copy className="w-[15px] h-[15px]" />}
                                              </button>
                                              <button
                                                type="button"
                                                onClick={() => speakText(displayText)}
                                                className="w-8 h-8 rounded-full flex items-center justify-center text-slate-400 hover:text-[#f8fafc] hover:bg-white/[0.08] transition-colors"
                                                title="Listen"
                                              >
                                                <Volume2 className="w-[15px] h-[15px]" />
                                              </button>

                                              <div className="w-[1px] h-3.5 bg-white/[0.12] mx-1" />

                                              {/* Roamies v0.1.46 "Good" Reinforcement */}
                                              <button
                                                type="button"
                                                onClick={() => handleFeedback("good")}
                                                className={`w-8 h-8 rounded-full flex items-center justify-center transition-colors ${
                                                  feedbackState === "good"
                                                    ? "bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40"
                                                    : "text-slate-400 hover:text-emerald-300 hover:bg-emerald-500/10"
                                                }`}
                                                title="Good - accurate & helpful"
                                              >
                                                {feedbackState === "good" ? (
                                                  <Check className="w-[14px] h-[14px] text-emerald-400" />
                                                ) : (
                                                  <ThumbsUp className="w-[14px] h-[14px]" />
                                                )}
                                              </button>

                                              {/* Roamies v0.1.46 "Wrong" Reinforcement */}
                                              <button
                                                type="button"
                                                onClick={() => setFeedbackState(feedbackState === "wrong_input" ? "idle" : "wrong_input")}
                                                className={`w-8 h-8 rounded-full flex items-center justify-center transition-colors ${
                                                  feedbackState === "wrong_input" || feedbackState === "wrong_submitted"
                                                    ? "bg-rose-500/20 text-rose-300 ring-1 ring-rose-500/40"
                                                    : "text-slate-400 hover:text-rose-300 hover:bg-rose-500/10"
                                                }`}
                                                title="Wrong - teach Amigo"
                                              >
                                                <ThumbsDown className="w-[14px] h-[14px]" />
                                              </button>

                                              <div className="w-[1px] h-3.5 bg-white/[0.12] mx-1" />

                                              <button
                                                type="button"
                                                onClick={handleDismissResponse}
                                                className="w-8 h-8 rounded-full flex items-center justify-center text-slate-400 hover:text-[#f8fafc] hover:bg-white/[0.08] transition-colors"
                                                title="Dismiss"
                                              >
                                                <X className="w-[15px] h-[15px]" />
                                              </button>
                                            </div>

                                            {/* Roamies Inline Correction Bar */}
                                            {feedbackState === "wrong_input" && (
                                              <div className="w-full max-w-[460px] mt-2.5 px-3 py-1.5 rounded-full bg-black/60 border border-white/[0.12] shadow-2xl backdrop-blur-md flex items-center gap-2 animate-in fade-in zoom-in-95 duration-200">
                                                <input
                                                  type="text"
                                                  value={correctionInput}
                                                  onChange={(e) => setCorrectionInput(e.target.value)}
                                                  onKeyDown={(e) => {
                                                    if (e.key === "Enter" && correctionInput.trim()) {
                                                      handleFeedback("wrong", correctionInput.trim());
                                                    } else if (e.key === "Escape") {
                                                      setFeedbackState("idle");
                                                    }
                                                  }}
                                                  autoFocus
                                                  placeholder="What should Amigo have done? (press Enter)"
                                                  className="flex-1 bg-transparent text-[13px] text-[#f1f5f9] placeholder:text-slate-500 outline-none px-1"
                                                />
                                                <button
                                                  type="button"
                                                  onClick={() => {
                                                    if (correctionInput.trim()) {
                                                      handleFeedback("wrong", correctionInput.trim());
                                                    }
                                                  }}
                                                  disabled={!correctionInput.trim()}
                                                  className="px-2.5 py-0.5 rounded-full text-[11.5px] font-medium bg-[#2dd4bf]/20 text-[#2dd4bf] hover:bg-[#2dd4bf]/30 disabled:opacity-40 transition-colors"
                                                >
                                                  Teach
                                                </button>
                                                <button
                                                  type="button"
                                                  onClick={() => setFeedbackState("idle")}
                                                  className="p-1 rounded-full text-slate-400 hover:text-slate-200 hover:bg-white/[0.08]"
                                                >
                                                  <X className="w-3.5 h-3.5" />
                                                </button>
                                              </div>
                                            )}

                                            {feedbackState === "wrong_submitted" && (
                                              <div className="text-[12px] text-emerald-400 mt-2 font-mono flex items-center gap-1.5 animate-in fade-in duration-200">
                                                <Check className="w-3.5 h-3.5" /> Correction saved to memory
                                              </div>
                                            )}
                                          </div>
                                        </div>
                                      );
                                    }

                                    return (
                                      <div className="text-center w-full max-w-2xl mx-auto px-2">
                                        <div
                                          className={`group relative inline-flex flex-col items-center justify-start select-none max-h-[46vh] overflow-y-auto no-scrollbar px-2 ${state === "idle" ? "cursor-pointer" : ""
                                            }`}
                                          onClick={state === "idle" ? handleShuffleGreeting : undefined}
                                          title={state === "idle" ? "Click to cycle greeting phrase" : undefined}
                                        >
                                          <KineticHeading
                                            key={`${turnId}-${state}`}
                                            turnId={turnId}
                                            text={
                                              state === "processing" || state === "working"
                                                ? (activePrompt || "Thinking...")
                                                : (displayText || greetingText)
                                            }
                                            isDark={isDark}
                                            colorTheme={colorTheme}
                                            animationStyle={textAnimationStyle}
                                            className={`${(() => {
                                                const raw = displayText || "";
                                                const len = raw.length;
                                                const words = raw.trim().split(/\s+/).filter(Boolean).length;
                                                if (words > 22 || len > 90) {
                                                  return "text-sm sm:text-base md:text-lg leading-relaxed font-normal";
                                                }
                                                if (words > 8 || len > 36) {
                                                  return "text-base sm:text-lg md:text-xl leading-relaxed font-medium";
                                                }
                                                return "text-xl sm:text-2xl md:text-3xl font-medium tracking-tight leading-snug";
                                              })()
                                              } group-hover:opacity-90 transition-opacity`}
                                          />
                                          {state === "idle" && (
                                            <span className="opacity-0 group-hover:opacity-60 transition-opacity text-[10px] mt-1.5 flex items-center space-x-1 font-medium tracking-wide">
                                              <Shuffle className="w-2.5 h-2.5" />
                                              <span>Click to shuffle greeting</span>
                                            </span>
                                          )}
                                        </div>
                                      </div>
                                    );
                                  })()}
                                </div>
                              )}

                              {/* Interactive State Cards Section */}
                              <div className="w-full flex items-center justify-center">
                                <AnimatePresence mode="wait">
                                  {/* Assistant UI ErrorState Component */}
                                  {errorState && (
                                    <motion.div
                                      key="error-state-card"
                                      initial={{ opacity: 0, scale: 0.95, y: 10 }}
                                      animate={{ opacity: 1, scale: 1, y: 0 }}
                                      exit={{ opacity: 0, scale: 0.95, y: -10 }}
                                      transition={{ duration: 0.2 }}
                                      className="w-full max-w-md mx-auto my-3 pointer-events-auto z-30 flex justify-center"
                                    >
                                      <ErrorState
                                        title={errorState.title}
                                        detail={errorState.detail}
                                        retrying={isRetrying}
                                        onRetry={handleRetryError}
                                        className="w-full border border-red-500/20 shadow-xl backdrop-blur-md"
                                      />
                                    </motion.div>
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

                                  {/* 4. Generated Content Panel (for reviewing generated content before inserting) */}
                                  {state === "generated_content" && generatedContent && (
                                    <Suspense fallback={null}>
                                      <GeneratedContentPanel
                                        key="generated-content-panel"
                                        content={generatedContent.content}
                                        contentType={generatedContent.contentType}
                                        topic={generatedContent.topic}
                                        onInsert={handleGeneratedContentInsert}
                                        onCopy={handleGeneratedContentCopy}
                                        onRegenerate={handleGeneratedContentRegenerate}
                                        onSave={handleGeneratedContentSave}
                                        onClose={handleGeneratedContentClose}
                                        isDark={isDark}
                                        colorTheme={colorTheme}
                                      />
                                    </Suspense>
                                  )}
                                </AnimatePresence>
                              </div>
                            </div>
                          </div>

                          {/* Docked Dynamic Action Capsule below 3D Visualizer Orb */}
                          <div className="absolute bottom-3 sm:bottom-4 inset-x-0 z-20 flex justify-center pointer-events-none">
                            <AnimatePresence mode="wait">
                              {state !== "contact_picker" &&
                                hudActive &&
                                !hudDismissed && (
                                  <IntentBridgeHUD
                                    key="intent-bridge-hud"
                                    prompt={activePrompt}
                                    isDark={isDark}
                                    colorTheme={colorTheme}
                                    intent={liveIntent || assistantData?.intent}
                                    params={liveParams || assistantData?.params || assistantData?.metadata?.params}
                                    historyCount={history.length}
                                    isCompleted={state === "completed" || (!loading && Boolean(assistantData))}
                                    status={assistantData?.executionSummary?.status}
                                    onDismiss={() => {
                                      setHudDismissed(true);
                                      setHudActive(false);
                                    }}
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
                      );
                    })()}

                    {/* Bottom Interactive Voice & Text Bar with Chips */}
                    <VoiceControls
                      onProcessCommand={handleProcessCommand}
                      isListening={isListening}
                      onSetListening={handleSetListening}
                      onTranscriptChange={setLiveTranscript}
                      isDark={isDark}
                      colorTheme={colorTheme}
                      isLoading={loading || state === "working" || state === "processing"}
                      loadingText={state === "working" ? "Working on task..." : "Searching..."}
                      disabled={loading || state === "working"}
                      backendConfig={backendConfig}
                    />
                  </motion.div>
                ) : (
                  <motion.div
                    key="chat-view"
                    initial={{ opacity: 0, y: 16, filter: "blur(4px)" }}
                    animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                    exit={{ opacity: 0, y: -16, filter: "blur(4px)" }}
                    transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
                    className={`relative flex-1 w-full min-h-0 flex flex-col overflow-hidden text-slate-100 ${isDark ? "bg-[#212121]" : "bg-white"
                      }`}
                  >
                    <ChatWorkspace
                      history={history}
                      onProcessCommand={handleProcessCommand}
                      onClearHistory={handleClearHistory}
                      isLoading={loading || state === "working" || state === "processing"}
                      isDark={isDark}
                      colorTheme={colorTheme}
                      backendConfig={backendConfig}
                      onSwitchToAssistant={() => handleSwitchViewMode("assistant")}
                      userName={userName}
                      assistantState={state}
                      liveIntent={liveIntent || assistantData?.intent}
                      liveParams={liveParams}
                      activePrompt={activePrompt}
                    />
                  </motion.div>
                )}
              </AnimatePresence>
            </main>
          </motion.div>
        )}
      </AnimatePresence>
      <iframe
        ref={overlayRef}
        src="/overlay/"
        title="Roamies Ink Overlay"
        className={`fixed inset-0 w-full h-full border-0 z-50 bg-transparent ${overlayHot ? "pointer-events-auto" : "pointer-events-none"}`}
        style={{ backgroundColor: "transparent", colorScheme: "dark" }}
      />
    </SparklesBackground>
  );
}
