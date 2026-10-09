import React, { useState, useEffect, useCallback, useRef } from "react";
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
import { GeneratedContentPanel } from "./components/GeneratedContentPanel";
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
} from "./services/assistantApi";
import { Sparkles, ChevronUp, Shuffle, Copy, Check, Volume2, X } from "lucide-react";

function cn(...classes: (string | undefined | null | false)[]) {
  return classes.filter(Boolean).join(" ");
}

interface AnimatedGradientBackgroundProps {
  className?: string;
  children?: React.ReactNode;
  intensity?: "subtle" | "medium" | "strong";
  isDark?: boolean;
}

interface Beam {
  x: number;
  y: number;
  width: number;
  length: number;
  angle: number;
  speed: number;
  opacity: number;
  hue: number;
  pulse: number;
  pulseSpeed: number;
}

function createBeam(width: number, height: number, isDarkMode: boolean): Beam {
  const angle = -35 + Math.random() * 10;
  const hueBase = isDarkMode ? 190 : 210;
  const hueRange = isDarkMode ? 70 : 50;

  return {
    x: Math.random() * width * 1.5 - width * 0.25,
    y: Math.random() * height * 1.5 - height * 0.25,
    width: 30 + Math.random() * 60,
    length: height * 2.5,
    angle,
    speed: 0.6 + Math.random() * 1.2,
    opacity: 0.12 + Math.random() * 0.16,
    hue: hueBase + Math.random() * hueRange,
    pulse: Math.random() * Math.PI * 2,
    pulseSpeed: 0.02 + Math.random() * 0.03,
  };
}

function BeamsBackground({
  className,
  intensity = "strong",
  isDark,
  children,
}: AnimatedGradientBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const beamsRef = useRef<Beam[]>([]);
  const animationFrameRef = useRef<number>(0);
  const MINIMUM_BEAMS = 20;
  const isDarkModeRef = useRef<boolean>(typeof isDark === "boolean" ? isDark : false);

  const opacityMap = {
    subtle: 0.7,
    medium: 0.85,
    strong: 1,
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Check for dark mode
    const updateDarkMode = () => {
      isDarkModeRef.current =
        typeof isDark === "boolean"
          ? isDark
          : document.documentElement.classList.contains("dark");
    };

    const observer = new MutationObserver(updateDarkMode);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    updateDarkMode();

    const updateCanvasSize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = window.innerWidth * dpr;
      canvas.height = window.innerHeight * dpr;
      canvas.style.width = `${window.innerWidth}px`;
      canvas.style.height = `${window.innerHeight}px`;
      ctx.scale(dpr, dpr);

      const totalBeams = MINIMUM_BEAMS * 1.5;
      beamsRef.current = Array.from({ length: totalBeams }, () =>
        createBeam(canvas.width, canvas.height, isDarkModeRef.current)
      );
    };

    updateCanvasSize();
    window.addEventListener("resize", updateCanvasSize);

    function resetBeam(beam: Beam, index: number, totalBeams: number) {
      if (!canvas) return beam;

      const column = index % 3;
      const spacing = canvas.width / 3;

      const hueBase = isDarkModeRef.current ? 190 : 210;
      const hueRange = isDarkModeRef.current ? 70 : 50;

      beam.y = canvas.height + 100;
      beam.x =
        column * spacing + spacing / 2 + (Math.random() - 0.5) * spacing * 0.5;
      beam.width = 100 + Math.random() * 100;
      beam.speed = 0.5 + Math.random() * 0.4;
      beam.hue = hueBase + (index * hueRange) / totalBeams;
      beam.opacity = 0.2 + Math.random() * 0.1;
      return beam;
    }

    function drawBeam(ctx: CanvasRenderingContext2D, beam: Beam) {
      ctx.save();
      ctx.translate(beam.x, beam.y);
      ctx.rotate((beam.angle * Math.PI) / 180);

      const pulsingOpacity =
        beam.opacity *
        (0.8 + Math.sin(beam.pulse) * 0.2) *
        opacityMap[intensity];

      const gradient = ctx.createLinearGradient(0, 0, 0, beam.length);

      const saturation = isDarkModeRef.current ? "85%" : "75%";
      const lightness = isDarkModeRef.current ? "65%" : "45%";

      gradient.addColorStop(
        0,
        `hsla(${beam.hue}, ${saturation}, ${lightness}, 0)`
      );
      gradient.addColorStop(
        0.1,
        `hsla(${beam.hue}, ${saturation}, ${lightness}, ${
          pulsingOpacity * 0.5
        })`
      );
      gradient.addColorStop(
        0.4,
        `hsla(${beam.hue}, ${saturation}, ${lightness}, ${pulsingOpacity})`
      );
      gradient.addColorStop(
        0.6,
        `hsla(${beam.hue}, ${saturation}, ${lightness}, ${pulsingOpacity})`
      );
      gradient.addColorStop(
        0.9,
        `hsla(${beam.hue}, ${saturation}, ${lightness}, ${
          pulsingOpacity * 0.5
        })`
      );
      gradient.addColorStop(
        1,
        `hsla(${beam.hue}, ${saturation}, ${lightness}, 0)`
      );

      ctx.fillStyle = gradient;
      ctx.fillRect(-beam.width / 2, 0, beam.width, beam.length);
      ctx.restore();
    }

    function animate() {
      if (!(canvas && ctx)) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const totalBeams = beamsRef.current.length;
      beamsRef.current.forEach((beam, index) => {
        beam.y -= beam.speed;
        beam.pulse += beam.pulseSpeed;

        // Reset beam when it goes off screen
        if (beam.y + beam.length < -100) {
          resetBeam(beam, index, totalBeams);
        }

        drawBeam(ctx, beam);
      });

      animationFrameRef.current = requestAnimationFrame(animate);
    }

    animate();

    return () => {
      window.removeEventListener("resize", updateCanvasSize);
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
      observer.disconnect();
    };
  }, [intensity, isDark]);

  return (
    <div
      className={cn(
        "relative min-h-screen w-full overflow-hidden bg-neutral-100 dark:bg-neutral-950",
        className
      )}
    >
      <canvas
        className="absolute inset-0"
        ref={canvasRef}
        style={{ filter: "blur(15px)" }}
      />

      <div
        className="pointer-events-none absolute inset-0 bg-neutral-900/10 dark:bg-neutral-950/20"
      />

      {children}
    </div>
  );
}

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
    try { localStorage.setItem("windows11_voice_assistant_thinking_orb_style", thinkingOrbStyle); } catch (e) {}
  }, [thinkingOrbStyle]);


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

        // Restore latest conversation onto stage on mount if stage is currently idle/greeting
        if (reversed.length > 0 && !activePromptRef.current) {
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
    } catch (e) {}
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

  const handleDismissResponse = () => {
    sfx.playClick();
    setState("idle");
    setActivePrompt("");
    activePromptRef.current = "";
    setDisplayText(greetingText);
  };

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
                setIsListening(true);
                setState("listening");
              } else if (s === "idle") {
                setIsListening(false);
                if (!loadingRef.current) {
                  setState((current) => {
                    if (current === "action_card" || current === "completed" || current === "generated_content") {
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
                setActivePrompt(data.text);
                activePromptRef.current = data.text;
                setDisplayText(data.text);
                setState("processing");
                setHudDismissed(false);
                setLiveIntent(null);
                setLiveParams(null);
                setHudActive(isActionIntent(data.text));
              } else if (data.sender === "assistant" || data.sender === "amigo") {
                const userPrompt = data.user_query || activePromptRef.current || "Voice Command";
                const cleanUser = cleanHistoryPrompt(userPrompt);
                setActivePrompt(cleanUser);
                activePromptRef.current = cleanUser;
                setDisplayText(data.text);
                setState((current) => {
                  if (current === "action_card" || current === "contact_picker" || current === "generated_content") {
                    return current; // Don't aggressively override interactive UI states
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
              if (data.params) {
                setLiveParams(data.params);
              }
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
            }
          } catch (e) {}
        };
        es.onerror = () => {
          window.dispatchEvent(new CustomEvent("amigo_backend_status", { detail: { online: false } }));
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
    if (state === "idle" && autoCycleGreetings && !activePrompt) {
      const activeText = GREETING_PRESETS[greetingIndex]?.text || GREETING_PRESETS[0].text;
      setGreetingText(activeText);
      setDisplayText((current) => (GREETING_PRESETS.some((g) => g.text === current) ? activeText : current));
    }
  }, [greetingIndex, autoCycleGreetings, state, activePrompt]);

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
    setHudDismissed(true);
    setHudActive(false);
    setLiveIntent(null);
    setLiveParams(null);
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
    setLiveIntent(null);
    setLiveParams(null);
    setHudActive(isActionIntent(effectivePrompt));
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

      const finalIntent = data.intent || liveIntent;
      if (finalIntent && isActionIntent(effectivePrompt, finalIntent)) {
        setHudActive(true);
      } else if (finalIntent && !isActionIntent(effectivePrompt, finalIntent)) {
        setHudActive(false);
      }

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
    const hasActionCards = Boolean(
      responseData.actionCards &&
      responseData.actionCards.length > 0 &&
      responseData.actionCards.some((c: any) => c.type !== "file")
    );
    
    // Check for generated content in history
    if (responseData.showGeneratedPanel && responseData.generatedContent) {
      setGeneratedContent({
        content: responseData.generatedContent,
        contentType: responseData.contentType || "document",
        topic: responseData.contentTopic || entry.prompt,
      });
      setState("generated_content");
    } else {
      setState(hasActionCards ? "action_card" : responseData.requiresDisambiguation ? "contact_picker" : "completed");
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
    } catch (e) {}
  }, []);

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
    <BeamsBackground isDark={isDark} className="relative w-full h-[var(--visual-viewport-height,100dvh)] min-h-0 flex items-center justify-center">
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
            className={`flex flex-col transition-colors duration-500 bg-transparent ${
              isDark ? "text-white" : "text-slate-900"
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
                thinkingOrbStyle={thinkingOrbStyle}
                onChangeThinkingOrbStyle={setThinkingOrbStyle}
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
                onClose={handleCloseHistory}
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

              {/* State-driven Thinking Orb & Central Display layer */}
              {(() => {
                const isCompact = state === "action_card" || state === "contact_picker";
                return (
                  <div className="relative flex-1 w-full min-h-0 flex flex-col items-center justify-center overflow-hidden">
                    {/* Central Display Area with Orb above & Spoken / Greeting text cleanly below */}
                    <div
                      id="central-display-area"
                      className="relative z-20 flex-1 min-h-0 w-full max-w-4xl flex flex-col items-center justify-center px-4 py-4 sm:py-6 overflow-y-auto smooth-scroll-container bg-transparent transition-opacity duration-200"
                    >
                      <div className="w-full flex flex-col items-center justify-center my-auto transition-opacity duration-200 max-w-2xl">
                        {/* 3D Orb Avatar (Positioned above text, never covered) */}
                        <div
                          className={`relative flex items-center justify-center transition-all duration-500 ease-out ${
                            isCompact ? "h-0 opacity-0 overflow-hidden mb-0" : "h-[15rem] sm:h-[16rem] shrink-0 mb-3 sm:mb-4"
                          }`}
                        >
                          <CanvasVisualizer
                            mode={visualizerMode}
                            state={state}
                            colorTheme={colorTheme}
                            isDark={isDark}
                            compact={isCompact}
                            isPaused={showSettings || showBackendModal}
                            thinkingOrbStyle={thinkingOrbStyle}
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

                  {/* Dedicated Action Card Spoken Context */}
                  {state === "action_card" && displayText && displayText !== greetingText && (
                    <motion.div
                      initial={{ opacity: 0, y: -6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -6 }}
                      transition={{ duration: 0.2 }}
                      className="text-center px-4 max-w-xl mx-auto mb-3 pointer-events-auto"
                    >
                      <p className="text-base sm:text-lg md:text-xl font-medium tracking-tight leading-snug drop-shadow-[0_2px_12px_rgba(0,0,0,0.8)] text-slate-100">
                        {displayText}
                      </p>
                    </motion.div>
                  )}

                  {/* Main Central Spoken / Heading Text (for idle, listening, processing, working, completed) */}
                  {!isCompact && (
                    <div className="w-full max-w-2xl sm:max-w-3xl md:max-w-4xl mx-auto px-4 drop-shadow-[0_4px_24px_rgba(0,0,0,0.85)] pointer-events-auto">
                      {(state === "listening" || isListening) && liveTranscript ? (
                        <div className="flex min-w-0 w-full flex-col items-center px-2 text-center">
                          <p className="w-full min-w-0 max-w-2xl break-words text-center text-xl font-semibold leading-snug text-slate-100 sm:text-2xl md:text-3xl">
                            {liveTranscript}
                          </p>
                        </div>
                      ) : (state === "listening" || isListening) ? (
                        <div className="flex flex-col items-center space-y-1 text-center">
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
                      ) : (() => {
                        const isGreeting = GREETING_PRESETS.some((g) => g.text.toLowerCase() === (displayText || "").toLowerCase()) || !displayText;

                        return (
                          <div className="text-center w-full max-w-2xl mx-auto px-2">
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
                                  (() => {
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

                            {/* Clean, seamless action toolbar without any background box */}
                            {displayText && !isGreeting && state === "completed" && (
                              <div className="flex items-center justify-center gap-2 mt-2 opacity-60 hover:opacity-100 transition-opacity">
                                <button
                                  type="button"
                                  onClick={() => handleCopyResponse(displayText)}
                                  className="p-1 rounded text-slate-400 hover:text-slate-100 transition-colors"
                                  title="Copy text"
                                >
                                  {copiedResponse ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => speakText(displayText)}
                                  className="p-1 rounded text-slate-400 hover:text-slate-100 transition-colors"
                                  title="Listen"
                                >
                                  <Volume2 className="w-3.5 h-3.5" />
                                </button>
                                <button
                                  type="button"
                                  onClick={handleDismissResponse}
                                  className="p-1 rounded text-slate-400 hover:text-slate-100 transition-colors"
                                  title="Dismiss"
                                >
                                  <X className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            )}
                          </div>
                        );
                      })()}
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

                      {/* 4. Generated Content Panel (for reviewing generated content before inserting) */}
                      {state === "generated_content" && generatedContent && (
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
                      )}

                      {/* Interactive Selection Panels (when visualizer is compact) */}
                    </AnimatePresence>
                  </div>
                </div>
              </div>

              {/* Docked Dynamic Action Capsule below 3D Visualizer Orb (Zero Overlap & Zero Layout Shift) */}
              <div className="absolute bottom-3 sm:bottom-4 inset-x-0 z-20 flex justify-center pointer-events-none">
                <AnimatePresence mode="wait">
                  {state !== "action_card" &&
                    state !== "contact_picker" &&
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
            </main>
          </motion.div>
        )}
      </AnimatePresence>
    </BeamsBackground>
  );
}
