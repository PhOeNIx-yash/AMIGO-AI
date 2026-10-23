import React, { useState, useEffect, useRef, useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Sparkles,
  Send,
  Mic,
  Square,
  Loader2,
  Copy,
  Check,
  Volume2,
  VolumeX,
  RotateCcw,
  Paperclip,
  X,
  FileText,
  Image as ImageIcon,
  Table as TableIcon,
  BarChart3,
  TrendingUp,
  Bot,
  Trash2,
} from "lucide-react";
import { HaloSearchInput } from "@/components/ui/halo-search";
import { ErrorState } from "@/components/ui/error-state";
import { AiStatus, AiStatusState } from "@/components/ui/ai-status";
import {
  HistoryEntry,
  ColorTheme,
  AttachmentItem,
  BackendConfig,
  ActionCardItem,
} from "../types";
import { COLOR_THEMES } from "../data/presets";
import { speakText, sfx } from "../utils/audio";
import { uploadFileToBackend } from "../services/assistantApi";
import {
  DataVisualizationCard,
  ParsedTableData,
} from "./DataVisualizationCard";
import { ReminderCardPalette, isReminderItem } from "./ActionCard";

interface ChatWorkspaceProps {
  history: HistoryEntry[];
  onProcessCommand: (prompt: string, attachment?: AttachmentItem) => void;
  onClearHistory: () => void;
  isLoading: boolean;
  isDark: boolean;
  colorTheme?: ColorTheme;
  backendConfig?: BackendConfig;
  userName?: string;
  liveIntent?: string;
  liveParams?: Record<string, any> | null;
  activePrompt?: string;
}

// Time-of-day greeting generator with user name
export function getTimeGreeting(userName?: string): { headline: string; subtitle: string } {
  const hour = new Date().getHours();
  let salutation = "Good morning";
  if (hour >= 12 && hour < 17) {
    salutation = "Good afternoon";
  } else if (hour >= 17) {
    salutation = "Good evening";
  } else {
    salutation = "Good morning";
  }

  const cleanName = userName?.trim();
  const headline = cleanName ? `${salutation}, ${cleanName}` : salutation;
  return {
    headline,
    subtitle: "How can I help you today?",
  };
}

// Helper to normalize collapsed LLM markdown (e.g. '| |' row breaks, collapsed bullets, merged headers)
export function normalizeMarkdownText(raw: string): string {
  if (!raw) return "";
  let text = raw.replace(/\r\n/g, "\n");

  // 1. Expand collapsed table rows: '| |' or '||' -> '|\n|'
  text = text.replace(/\|\s*\|+/g, "|\n|");

  // 2. Separate leading intro title from table header on the same line:
  // e.g. "GPU Comparison: RTX 4090 | Feature | RTX 4080 |" -> "GPU Comparison: RTX 4090\n\n| Feature | RTX 4080 |"
  text = text.replace(/^([^|\n]+?)\s*(\|\s*[^|\n]+\|)/gm, "$1\n\n$2");

  // 3. Separate markdown header attached immediately after a table row:
  // e.g. "| 270 W | ## Key Takeaways" -> "| 270 W |\n\n## Key Takeaways"
  text = text.replace(/(\|\s*)(#{1,6}\s+)/g, "$1\n\n$2");

  // 4. Separate trailing non-pipe commentary at end of table row:
  // e.g. "| 270 W | Summary: The..." -> "| 270 W |\n\nSummary: The..."
  text = text.replace(/(\|)\s*([A-Za-z*#][^|\n]*)$/gm, "$1\n\n$2");

  // 5. Expand collapsed inline bullet points:
  // e.g. "edge. - Memory Bandwidth: ..." -> "edge.\n- Memory Bandwidth: ..."
  text = text.replace(/([^\n])\s+-\s+([A-Za-z0-9*])/g, "$1\n- $2");

  // 6. Expand collapsed numbered list items:
  // e.g. "done 1. First step" -> "done\n1. First step"
  text = text.replace(/([^\n])\s+(\d+\.)\s+([A-Za-z0-9*])/g, "$1\n$2 $3");

  return text;
}

// Helper to extract markdown tables and separate text
function extractTablesFromMarkdown(content: string): {
  segments: Array<
    | { type: "text"; text: string }
    | { type: "table"; table: ParsedTableData }
  >;
} {
  const normalized = normalizeMarkdownText(content || "");
  if (!normalized || !normalized.includes("|")) {
    return { segments: [{ type: "text", text: normalized || "" }] };
  }

  const lines = normalized.split("\n");
  const segments: Array<
    | { type: "text"; text: string }
    | { type: "table"; table: ParsedTableData }
  > = [];

  let currentTextLines: string[] = [];
  let tableLines: string[] = [];
  let inTable = false;

  const flushText = () => {
    if (currentTextLines.length > 0) {
      const text = currentTextLines.join("\n").trim();
      if (text) {
        segments.push({ type: "text", text });
      }
      currentTextLines = [];
    }
  };

  const flushTable = () => {
    if (tableLines.length >= 2) {
      const rawMarkdown = tableLines.join("\n");
      const cleanLines = tableLines
        .filter((l) => !/^\s*\|?[\s\-:|]{3,}\|[\s\-:|]*$/.test(l))
        .map((l) =>
          l
            .split("|")
            .map((c) => c.trim())
            .filter((c, i, arr) => (i === 0 || i === arr.length - 1 ? c !== "" : true))
        )
        .filter((row) => row.length > 0);

      if (cleanLines.length >= 2) {
        const headers = cleanLines[0];
        const rows = cleanLines.slice(1);
        segments.push({
          type: "table",
          table: {
            headers,
            rows,
            rawMarkdown,
          },
        });
        tableLines = [];
        inTable = false;
        return;
      }
    }

    // Fallback: wasn't a valid table, return as text
    if (tableLines.length > 0) {
      currentTextLines.push(...tableLines);
      tableLines = [];
    }
    inTable = false;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const isPipeLine =
      line.trim().startsWith("|") ||
      (line.includes("|") && line.trim().endsWith("|")) ||
      line.split("|").length >= 3;
    const isDividerLine = /^\s*\|?[\s\-:|]{3,}\|[\s\-:|]*$/.test(line);

    if (isPipeLine || isDividerLine) {
      if (!inTable) {
        flushText();
        inTable = true;
      }
      tableLines.push(line);
    } else {
      if (inTable) {
        flushTable();
      }
      currentTextLines.push(line);
    }
  }

  if (inTable) {
    flushTable();
  }
  flushText();

  return { segments: segments.length ? segments : [{ type: "text", text: normalized }] };
}

// Markdown Formatter Component with code blocks and styled elements
const FormattedMessage: React.FC<{
  content: string;
  isDark: boolean;
  colorTheme?: ColorTheme;
}> = ({ content, isDark, colorTheme }) => {
  const { segments } = useMemo(() => extractTablesFromMarkdown(content), [content]);

  return (
    <div className="space-y-3 leading-relaxed text-sm">
      {segments.map((seg, idx) => {
        if (seg.type === "table") {
          return (
            <DataVisualizationCard
              key={`table-${idx}`}
              tableData={seg.table}
              colorTheme={colorTheme}
              isDark={isDark}
            />
          );
        }

        const lines = seg.text.split("\n");
        return (
          <div key={`text-${idx}`} className="space-y-2">
            {lines.map((line, lIdx) => {
              const trimmed = line.trim();
              if (!trimmed) return <div key={lIdx} className="h-2" />;

              // Headers
              if (trimmed.startsWith("### ")) {
                return (
                  <h4 key={lIdx} className="text-sm font-bold tracking-tight text-slate-100 mt-2">
                    {trimmed.replace(/^###\s+/, "")}
                  </h4>
                );
              }
              if (trimmed.startsWith("## ")) {
                return (
                  <h3 key={lIdx} className="text-base font-bold tracking-tight text-slate-100 mt-2.5">
                    {trimmed.replace(/^##\s+/, "")}
                  </h3>
                );
              }
              if (trimmed.startsWith("# ")) {
                return (
                  <h2 key={lIdx} className="text-lg font-bold tracking-tight text-slate-100 mt-3">
                    {trimmed.replace(/^#\s+/, "")}
                  </h2>
                );
              }

              // Bullet points
              if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
                return (
                  <div key={lIdx} className="flex items-start space-x-2 pl-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 mt-2 shrink-0" />
                    <span>{renderInlineMarkdown(trimmed.replace(/^[-*]\s+/, ""))}</span>
                  </div>
                );
              }

              // Numbered lists
              const numMatch = trimmed.match(/^(\d+)\.\s+(.+)$/);
              if (numMatch) {
                return (
                  <div key={lIdx} className="flex items-start space-x-2 pl-2">
                    <span className="font-mono text-xs font-semibold text-indigo-400 mt-0.5 shrink-0">
                      {numMatch[1]}.
                    </span>
                    <span>{renderInlineMarkdown(numMatch[2])}</span>
                  </div>
                );
              }

              return <p key={lIdx}>{renderInlineMarkdown(trimmed)}</p>;
            })}
          </div>
        );
      })}
    </div>
  );
};

// Inline parser for markdown links, raw URLs, bold, italic, code
function renderInlineMarkdown(text: string): React.ReactNode {
  const parts = text.split(/(\[[^\]]+\]\(https?:\/\/[^\s\)]+\)|https?:\/\/[^\s\)]+|\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g);
  return parts.map((part, index) => {
    const mdLink = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)$/);
    if (mdLink) {
      return (
        <a
          key={index}
          href={mdLink[2]}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-cyan-400 hover:text-cyan-300 underline underline-offset-2 transition-colors cursor-pointer"
        >
          {mdLink[1]}
        </a>
      );
    }
    if (/^https?:\/\/[^\s\)]+$/.test(part)) {
      return (
        <a
          key={index}
          href={part}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-cyan-400 hover:text-cyan-300 underline underline-offset-2 break-all transition-colors cursor-pointer"
        >
          {part}
        </a>
      );
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={index} className="font-semibold text-slate-100">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={index}
          className="px-1.5 py-0.5 rounded text-xs font-mono bg-black/40 text-pink-400 border border-white/10"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("*") && part.endsWith("*")) {
      return <em key={index}>{part.slice(1, -1)}</em>;
    }
    return part;
  });
}

export const ChatWorkspace: React.FC<ChatWorkspaceProps> = ({
  history,
  onProcessCommand,
  onClearHistory,
  isLoading,
  isDark,
  colorTheme = "beams",
  userName = "",
  liveIntent,
  liveParams,
  activePrompt,
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.beams || COLOR_THEMES.violet;
  const [inputText, setInputText] = useState("");
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [attachment, setAttachment] = useState<AttachmentItem | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState("");
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const speechRecognitionRef = useRef<any>(null);

  // Dynamically compute AI status: state, dynamic action label, and specific detail
  const statusInfo = useMemo(() => {
    if (!isLoading) {
      return {
        state: "done" as AiStatusState,
        label: "Done",
        detail: undefined,
      };
    }

    const intent = (liveIntent || "").trim().toLowerCase();
    const serverLabel = liveParams?.label;
    const serverDetail = liveParams?.detail;

    // 1. If server explicitly supplied label/detail via SSE intent_detected:
    if (serverLabel) {
      let mappedState: AiStatusState = "thinking";
      const sl = String(serverLabel).toLowerCase();
      if (
        intent.includes("open") ||
        sl.includes("browsing") ||
        intent.includes("website")
      ) {
        mappedState = "browsing";
      } else if (
        intent.includes("search") ||
        sl.includes("search") ||
        intent.includes("weather") ||
        intent.includes("youtube")
      ) {
        mappedState = "researching";
      } else if (
        intent.includes("doc") ||
        intent.includes("file") ||
        intent.includes("screen") ||
        intent.includes("stock") ||
        sl.includes("analyz") ||
        sl.includes("quote")
      ) {
        mappedState = "analyzing";
      } else if (
        intent.includes("content") ||
        intent.includes("email") ||
        sl.includes("draft") ||
        sl.includes("writ")
      ) {
        mappedState = "writing";
      }

      return {
        state: mappedState,
        label: String(serverLabel),
        detail:
          serverDetail ||
          (activePrompt
            ? activePrompt.length > 38
              ? activePrompt.slice(0, 38) + "..."
              : activePrompt
            : undefined),
      };
    }

    // 2. Direct tool intent mapping:
    if (intent === "web_search") {
      return {
        state: "researching" as AiStatusState,
        label: "Searching Web",
        detail:
          (liveParams?.query as string) ||
          (activePrompt
            ? activePrompt.length > 38
              ? activePrompt.slice(0, 38) + "..."
              : activePrompt
            : undefined),
      };
    }
    if (intent === "open_website") {
      return {
        state: "browsing" as AiStatusState,
        label: "Browsing",
        detail:
          (liveParams?.title as string) ||
          (liveParams?.url as string) ||
          "Opening link",
      };
    }
    if (
      intent === "document_qa" ||
      intent === "ask_document" ||
      intent === "summarize_document"
    ) {
      return {
        state: "analyzing" as AiStatusState,
        label: "Analyzing Documents",
        detail:
          (liveParams?.filepath as string) ||
          (liveParams?.query as string) ||
          "Searching indexed notes",
      };
    }
    if (
      intent === "generate_content" ||
      intent === "regenerate_content" ||
      intent === "draft_email"
    ) {
      return {
        state: "writing" as AiStatusState,
        label: "Drafting",
        detail:
          (liveParams?.topic as string) ||
          (liveParams?.type as string) ||
          "Formulating draft",
      };
    }
    if (intent === "stock_quote") {
      return {
        state: "analyzing" as AiStatusState,
        label: "Market Quote",
        detail: liveParams?.symbol
          ? `Quote: ${String(liveParams.symbol).toUpperCase()}`
          : "Fetching live price",
      };
    }
    if (intent === "get_weather") {
      return {
        state: "researching" as AiStatusState,
        label: "Checking Weather",
        detail: (liveParams?.city as string) || "Local forecast",
      };
    }
    if (intent === "play_youtube") {
      return {
        state: "researching" as AiStatusState,
        label: "Streaming Media",
        detail: (liveParams?.query as string) || "YouTube",
      };
    }
    if (intent === "read_screen" || intent === "screen_vision") {
      return {
        state: "analyzing" as AiStatusState,
        label: "Vision Analysis",
        detail: "Inspecting active screen",
      };
    }
    if (intent === "find_file" || intent === "open_file") {
      return {
        state: "analyzing" as AiStatusState,
        label: "Finding File",
        detail: (liveParams?.query as string) || "Scanning file system",
      };
    }

    // 3. Fallback heuristic from prompt text:
    const promptText = `${activePrompt || ""} ${inputText || ""}`.toLowerCase();
    if (
      promptText.includes("search") ||
      promptText.includes("google") ||
      promptText.includes("flight") ||
      promptText.includes("amazon") ||
      promptText.includes("buy") ||
      promptText.includes("weather")
    ) {
      return {
        state: "researching" as AiStatusState,
        label: "Researching",
        detail: activePrompt
          ? activePrompt.length > 38
            ? activePrompt.slice(0, 38) + "..."
            : activePrompt
          : undefined,
      };
    }
    if (
      promptText.includes("analyze") ||
      promptText.includes("pdf") ||
      promptText.includes("file") ||
      promptText.includes("doc") ||
      promptText.includes("calculate") ||
      promptText.includes("table")
    ) {
      return {
        state: "analyzing" as AiStatusState,
        label: "Analyzing",
        detail: activePrompt
          ? activePrompt.length > 38
            ? activePrompt.slice(0, 38) + "..."
            : activePrompt
          : undefined,
      };
    }
    if (
      promptText.includes("write") ||
      promptText.includes("draft") ||
      promptText.includes("code") ||
      promptText.includes("compose") ||
      promptText.includes("email")
    ) {
      return {
        state: "writing" as AiStatusState,
        label: "Writing",
        detail: activePrompt
          ? activePrompt.length > 38
            ? activePrompt.slice(0, 38) + "..."
            : activePrompt
          : undefined,
      };
    }

    return {
      state: "thinking" as AiStatusState,
      label: "Thinking",
      detail: activePrompt
        ? activePrompt.length > 38
          ? activePrompt.slice(0, 38) + "..."
          : activePrompt
        : undefined,
    };
  }, [isLoading, liveIntent, liveParams, activePrompt, inputText]);

  useEffect(() => {
    if (!isLoading) {
      setRetryingId(null);
    }
  }, [isLoading]);

  // Auto-scroll to bottom on new messages or loading change
  useEffect(() => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTo({
        top: scrollContainerRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [history, isLoading]);

  // Handle command submit
  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const prompt = inputText.trim();
    if (!prompt && !attachment) return;

    sfx.playSubmit();
    onProcessCommand(prompt, attachment || undefined);
    setInputText("");
    setAttachment(null);
  };

  const handleCopyMessage = (id: string, text: string) => {
    sfx.playClick?.();
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleToggleSpeak = (id: string, text: string) => {
    sfx.playClick?.();
    if (speakingId === id) {
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
      setSpeakingId(null);
    } else {
      setSpeakingId(id);
      speakText(text);
    }
  };

  // File upload handler
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    sfx.playClick?.();
    setIsUploading(true);
    const item: AttachmentItem = {
      id: `attach-${Date.now()}`,
      file,
      filename: file.name,
      fileType: file.type.startsWith("image/")
        ? "image"
        : file.name.endsWith(".pdf")
        ? "pdf"
        : "document",
      size: file.size,
      uploadStatus: "uploading",
    };
    setAttachment(item);

    try {
      const res = await uploadFileToBackend(file);
      if (res.success) {
        setAttachment((prev) => (prev ? { ...prev, uploadStatus: "success", filepath: res.filepath } : null));
      } else {
        setAttachment((prev) => (prev ? { ...prev, uploadStatus: "error", error: res.error } : null));
      }
    } catch {
      setAttachment((prev) => (prev ? { ...prev, uploadStatus: "error" } : null));
    } finally {
      setIsUploading(false);
    }
  };

  // Voice dictation toggle in chat mode
  const handleToggleMic = () => {
    sfx.playClick?.();
    if (isListening) {
      try {
        speechRecognitionRef.current?.stop();
      } catch {}
      setIsListening(false);
      return;
    }

    const SpeechRec = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRec) {
      alert("Speech recognition is not supported in this browser.");
      return;
    }

    try {
      const rec = new SpeechRec();
      rec.continuous = false;
      rec.interimResults = true;
      rec.lang = "en-US";

      rec.onstart = () => {
        setIsListening(true);
        setLiveTranscript("");
      };

      rec.onresult = (evt: any) => {
        let transcript = "";
        for (let i = 0; i < evt.results.length; i++) {
          transcript += evt.results[i][0].transcript;
        }
        setLiveTranscript(transcript);
        setInputText(transcript);
      };

      rec.onerror = () => {
        setIsListening(false);
      };

      rec.onend = () => {
        setIsListening(false);
      };

      speechRecognitionRef.current = rec;
      rec.start();
    } catch {
      setIsListening(false);
    }
  };

  // Chronological messages list (oldest to newest)
  const messages = useMemo(() => {
    return [...history].reverse();
  }, [history]);

  const quickPrompts = [
    {
      title: "Flight Pricing Comparison",
      prompt: "Show me a comparison table of 5 flights from JFK to London with airline, duration, and price in USD",
      icon: <TableIcon className="w-4 h-4 text-sky-400" />,
    },
    {
      title: "Programming Languages Benchmark",
      prompt: "Generate a table of top 5 programming languages with popularity score from 1-100, average salary in USD, and release year",
      icon: <BarChart3 className="w-4 h-4 text-emerald-400" />,
    },
    {
      title: "Quarterly Revenue Growth",
      prompt: "Create a quarterly revenue trend table for a tech startup from Q1 to Q4 with revenue in thousands and profit margin percentage",
      icon: <TrendingUp className="w-4 h-4 text-violet-400" />,
    },
    {
      title: "GPU vs CPU Performance",
      prompt: "Compare Nvidia RTX 4090, RTX 4080, and Apple M3 Max in a table with TFLOPS compute, memory bandwidth GB/s, and TDP watts",
      icon: <TableIcon className="w-4 h-4 text-amber-400" />,
    },
  ];

  return (
    <div
      className={`relative flex-1 flex flex-col w-full h-full min-h-0 overflow-hidden select-text transition-colors duration-300 ${
        isDark ? "bg-[#212121] text-slate-100" : "bg-white text-slate-900"
      }`}
    >
      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        onChange={handleFileChange}
        className="hidden"
        accept="*/*"
      />

      {/* Main Conversation Stream */}
      <div
        ref={scrollContainerRef}
        className="flex-1 min-h-0 overflow-y-auto px-4 py-6 space-y-6 smooth-scroll-container bg-transparent"
      >
        <div className="max-w-4xl lg:max-w-5xl mx-auto w-full space-y-6">
          {/* Top Status Bar: AiStatus Badge & Clear Conversation Button */}
          <div className="flex items-center justify-between pb-1 px-1">
            <div className="flex items-center space-x-2">
              <AiStatus
                state={isLoading ? statusInfo.state : "done"}
                label={isLoading ? statusInfo.label : "Amigo Ready"}
                detail={isLoading ? statusInfo.detail : undefined}
                timer={isLoading}
                shimmer={isLoading}
                className="scale-90 origin-left text-xs"
              />
            </div>
            {messages.length > 0 && (
              <button
                type="button"
                onClick={() => {
                  sfx.playClick();
                  onClearHistory();
                }}
                className={`text-[11px] opacity-40 hover:opacity-100 flex items-center space-x-1.5 transition-opacity px-2 py-1 rounded-md ${
                  isDark ? "text-slate-400 hover:text-white" : "text-slate-500 hover:text-black"
                }`}
                title="Clear conversation"
              >
                <Trash2 className="w-3 h-3" />
                <span>Clear chat</span>
              </button>
            )}
          </div>
          {/* Empty State / Welcome Screen */}
          {messages.length === 0 && (
            <div className="py-8 flex flex-col items-center justify-center text-center space-y-6">
              <div
                className="w-16 h-16 rounded-2xl flex items-center justify-center text-white shadow-xl relative"
                style={{
                  background: theme.gradient,
                  boxShadow: `0 0 35px ${theme.glow}`,
                }}
              >
                <Sparkles className="w-8 h-8 animate-pulse" />
              </div>

              {/* Time of Day Greeting with User Name */}
              {(() => {
                const greeting = getTimeGreeting(userName);
                return (
                  <div className="space-y-1.5 max-w-md">
                    <h2
                      className={`text-2xl sm:text-3xl font-bold tracking-tight ${
                        isDark ? "text-slate-100" : "text-slate-900"
                      }`}
                    >
                      {greeting.headline}
                    </h2>
                    <p
                      className={`text-sm sm:text-base font-medium ${
                        isDark ? "text-slate-400" : "text-slate-600"
                      }`}
                    >
                      {greeting.subtitle}
                    </p>
                  </div>
                );
              })()}

              {/* Quick Starter Prompts */}
              <div className="w-full grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-2 text-left">
                {quickPrompts.map((q, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => {
                      sfx.playClick?.();
                      setInputText(q.prompt);
                      onProcessCommand(q.prompt);
                    }}
                    className={`p-3.5 rounded-xl border text-left transition-all duration-200 group flex items-start space-x-3 ${
                      isDark
                        ? "bg-slate-900/60 hover:bg-slate-800/80 border-slate-800/70 hover:border-slate-700/80"
                        : "bg-white/80 hover:bg-white border-slate-200/80 hover:border-slate-300 shadow-sm"
                    }`}
                  >
                    <div
                      className={`p-2 rounded-lg border shrink-0 group-hover:scale-110 transition-transform ${
                        isDark ? "bg-slate-950/80 border-white/10" : "bg-slate-100 border-black/10"
                      }`}
                    >
                      {q.icon}
                    </div>
                    <div className="min-w-0">
                      <div
                        className={`text-xs font-semibold truncate ${
                          isDark
                            ? "text-slate-200 group-hover:text-white"
                            : "text-slate-800 group-hover:text-black"
                        }`}
                      >
                        {q.title}
                      </div>
                      <div
                        className={`text-[11px] line-clamp-2 mt-0.5 ${
                          isDark ? "text-slate-400" : "text-slate-500"
                        }`}
                      >
                        {q.prompt}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Chat Messages */}
          {messages.map((entry) => {
            const isError =
              entry.status === "error" ||
              entry.response.intent === "error" ||
              entry.response.executionSummary?.status === "error" ||
              entry.response.executionSummary?.status === "failed" ||
              Boolean(entry.response.metadata?.error);

            const errorTitle =
              entry.response.executionSummary?.headline ||
              (entry.response.intent === "error" ? "Execution Error" : "Command Error");

            const errorDetail =
              entry.response.metadata?.error ||
              entry.response.executionSummary?.details ||
              entry.response.speechReply ||
              "An error occurred while processing this command.";

            const replyText =
              entry.response.speechReply ||
              entry.response.executionSummary?.details ||
              entry.response.displayTitle ||
              "";

            const reminderCard: ActionCardItem | null =
              entry.response.actionCards?.find(isReminderItem) ||
              ((entry.response.intent === "set_reminder" || entry.response.intent === "list_reminders")
                ? {
                    id: `chat-rem-${entry.id}`,
                    title: "Reminders & Tasks",
                    type: "reminder",
                    badge: "reminder",
                    description: replyText,
                    selected: true,
                    actionType: "custom",
                    payload: {
                      tool: "reminder",
                      message: replyText,
                      reminders: entry.response.metadata?.reminders || [
                        { id: `rem-${entry.id}`, message: replyText || entry.prompt },
                      ],
                    },
                  }
                : null);

            return (
              <motion.div
                key={entry.id}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                className="space-y-4"
              >
                {/* User Message Bubble - Right-aligned ChatGPT style */}
                <div className="flex justify-end">
                  <div
                    className={`px-5 py-3 rounded-3xl max-w-[85%] sm:max-w-[75%] text-[15px] leading-relaxed shadow-sm ${
                      isDark
                        ? "bg-[#2f2f2f] text-slate-100"
                        : "bg-[#f4f4f4] text-slate-900 border border-slate-200/60"
                    }`}
                  >
                    <p className="whitespace-pre-wrap">{entry.prompt}</p>
                    <div className="mt-1 flex items-center justify-end text-[10px] opacity-40 font-mono">
                      {new Date(entry.timestamp).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </div>
                  </div>
                </div>

                {/* Assistant Message or Error State */}
                {isError ? (
                  <div className="flex items-start space-x-3.5 py-1">
                    <div
                      className="w-7 h-7 rounded-full flex items-center justify-center text-white shrink-0 mt-1 shadow-sm bg-red-500/80"
                    >
                      <Bot className="w-3.5 h-3.5" />
                    </div>

                    <div className="flex-1 min-w-0 space-y-2">
                      <ErrorState
                        title={errorTitle}
                        detail={errorDetail}
                        retrying={Boolean(isLoading && retryingId === entry.id)}
                        onRetry={() => {
                          setRetryingId(entry.id);
                          onProcessCommand(entry.prompt);
                        }}
                        className="max-w-lg border border-red-500/20 shadow-md backdrop-blur-sm"
                      />
                      <div className="flex items-center space-x-1.5 pt-1 text-xs opacity-60">
                        <span className="text-[10px] font-mono opacity-50 mr-2">
                          {entry.response.intent || "error"} •{" "}
                          {new Date(entry.timestamp).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </span>
                      </div>
                    </div>
                  </div>
                ) : replyText ? (
                  <div className="flex items-start space-x-3.5 py-1">
                    <div
                      className="w-7 h-7 rounded-full flex items-center justify-center text-white shrink-0 mt-1 shadow-sm"
                      style={{ background: theme.gradient }}
                    >
                      <Bot className="w-3.5 h-3.5" />
                    </div>

                    <div className="flex-1 min-w-0 space-y-2">
                      <div className="text-[15px] leading-relaxed">
                        <FormattedMessage
                          content={replyText}
                          isDark={isDark}
                          colorTheme={colorTheme}
                        />
                      </div>

                      {/* Interactive TaskList for Reminders & Tasks */}
                      {reminderCard && (
                        <div className="pt-2 max-w-lg w-full">
                          <ReminderCardPalette
                            item={reminderCard}
                            isDark={isDark}
                            theme={theme}
                          />
                        </div>
                      )}

                      {/* Message Action Toolbar */}
                      <div className="flex items-center justify-between pt-1 text-xs opacity-60 hover:opacity-100 transition-opacity">
                        <div className="flex items-center space-x-2">
                          <AiStatus
                            state={isError ? "error" : "done"}
                            label={isError ? "Failed" : (entry.response.intent ? entry.response.intent.replace(/_/g, " ") : "Done")}
                            timer={false}
                            className="scale-90 origin-left text-xs opacity-75"
                          />
                          <span className="text-[10px] font-mono opacity-50">
                            •{" "}
                            {new Date(entry.timestamp).toLocaleTimeString([], {
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </span>
                        </div>

                        <div className="flex items-center space-x-1.5">
                          <button
                            type="button"
                            onClick={() => handleCopyMessage(entry.id, replyText)}
                            className="p-1.5 rounded-lg hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
                            title="Copy response"
                          >
                            {copiedId === entry.id ? (
                              <Check className="w-3.5 h-3.5 text-emerald-400" />
                            ) : (
                              <Copy className="w-3.5 h-3.5" />
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleToggleSpeak(entry.id, replyText)}
                            className="p-1.5 rounded-lg hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
                            title={speakingId === entry.id ? "Stop voice" : "Read aloud"}
                          >
                            {speakingId === entry.id ? (
                              <VolumeX className="w-3.5 h-3.5 text-rose-400 animate-pulse" />
                            ) : (
                              <Volume2 className="w-3.5 h-3.5" />
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              sfx.playClick?.();
                              onProcessCommand(entry.prompt);
                            }}
                            className="p-1.5 rounded-lg hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
                            title="Re-run query"
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : null}
              </motion.div>
            );
          })}

          {/* Thinking / Searching / AI Status Indicator (Rare UI ai-status) */}
          {isLoading && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              style={{ willChange: "transform, opacity", transform: "translate3d(0, 0, 0)" }}
              className="flex items-center space-x-3 py-2 text-sm"
            >
              <div
                className="w-7 h-7 rounded-full flex items-center justify-center text-white shrink-0 shadow-sm animate-pulse transform-gpu"
                style={{ background: theme.gradient }}
              >
                <Sparkles className="w-3.5 h-3.5" />
              </div>
              <div
                className={`px-3.5 py-1.5 rounded-full border backdrop-blur-md shadow-sm transition-all duration-300 ${
                  isDark
                    ? "bg-slate-900/90 border-slate-800 text-slate-100 shadow-cyan-950/20"
                    : "bg-white/95 border-slate-200 text-slate-800 shadow-slate-200/50"
                }`}
              >
                <AiStatus
                  state={statusInfo.state}
                  label={statusInfo.label}
                  detail={statusInfo.detail}
                  timer={true}
                  shimmer={true}
                />
              </div>
            </motion.div>
          )}
        </div>
      </div>

      {/* Floating Bottom Input Zone */}
      <div className="px-4 pb-4 sm:pb-6 pt-2 z-20 shrink-0 bg-transparent">
        <div className="max-w-4xl lg:max-w-5xl mx-auto w-full space-y-2">
          {/* Attachment Preview Chip */}
          <AnimatePresence>
            {attachment && (
              attachment.uploadStatus === "error" ? (
                <motion.div
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 6 }}
                  className="w-full flex items-center justify-between pb-1"
                >
                  <ErrorState
                    title="Upload Failed"
                    detail={attachment.error || `Could not upload ${attachment.filename}`}
                    retrying={isUploading}
                    onRetry={() => {
                      if (attachment.file) {
                        const file = attachment.file;
                        setIsUploading(true);
                        setAttachment((prev) => (prev ? { ...prev, uploadStatus: "uploading", error: undefined } : null));
                        uploadFileToBackend(file)
                          .then((res) => {
                            if (res.success) {
                              setAttachment((prev) => (prev ? { ...prev, uploadStatus: "success", filepath: res.filepath } : null));
                            } else {
                              setAttachment((prev) => (prev ? { ...prev, uploadStatus: "error", error: res.error } : null));
                            }
                          })
                          .catch((err) => {
                            setAttachment((prev) => (prev ? { ...prev, uploadStatus: "error", error: String(err) } : null));
                          })
                          .finally(() => setIsUploading(false));
                      } else {
                        fileInputRef.current?.click();
                      }
                    }}
                    className="max-w-md border border-red-500/20"
                  />
                  <button
                    type="button"
                    onClick={() => setAttachment(null)}
                    className="p-1 rounded-full text-slate-400 hover:text-white ml-2"
                    title="Dismiss"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </motion.div>
              ) : (
                <motion.div
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 6 }}
                  className={`inline-flex items-center space-x-2 px-3 py-1 rounded-xl text-xs border ${
                    isDark
                      ? "bg-slate-800 border-white/10 text-slate-200"
                      : "bg-white border-black/10 text-slate-800 shadow-sm"
                  }`}
                >
                  {attachment.fileType === "image" ? (
                    <ImageIcon className="w-3.5 h-3.5 text-sky-400" />
                  ) : (
                    <FileText className="w-3.5 h-3.5 text-rose-400" />
                  )}
                  <span className="truncate max-w-[200px] font-medium">
                    {attachment.filename}
                  </span>
                  {isUploading ? (
                    <Loader2 className="w-3 h-3 animate-spin text-indigo-400" />
                  ) : (
                    <button
                      type="button"
                      onClick={() => setAttachment(null)}
                      className="p-0.5 hover:opacity-70"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </motion.div>
              )
            )}
          </AnimatePresence>

          {/* Cult UI Halo Search Input */}
          <form onSubmit={handleSubmit} className="w-full flex justify-center">
            <HaloSearchInput
              id="chat-halo-search-input"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onClear={() => setInputText("")}
              placeholder={
                attachment
                  ? `Ask Amigo about ${attachment.filename}...`
                  : "Ask Amigo anything, request tables, or generate graphs..."
              }
              isLoading={isLoading}
              loadingText="Searching & generating response..."
              isListening={isListening}
              listeningText={liveTranscript || "Listening..."}
              disabled={isLoading}
              className="w-full"
              leftSlot={
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className={`w-8 h-8 rounded-full flex items-center justify-center transition-colors border ${
                    isDark
                      ? "border-white/10 text-slate-300 hover:text-white hover:bg-white/10"
                      : "border-black/10 text-slate-600 hover:text-black hover:bg-black/5"
                  }`}
                  title="Attach file, image, or document"
                >
                  <Paperclip className="w-3.5 h-3.5" />
                </button>
              }
              rightSlot={
                <div className="flex items-center space-x-1.5">
                  {/* Voice dictation mic button */}
                  <button
                    type="button"
                    onClick={handleToggleMic}
                    className={`w-8 h-8 rounded-full flex items-center justify-center transition-all ${
                      isListening
                        ? "bg-rose-500 text-white shadow-lg animate-pulse"
                        : isDark
                        ? "hover:bg-white/10 text-slate-300 hover:text-white"
                        : "hover:bg-black/5 text-slate-600 hover:text-black"
                    }`}
                    title={isListening ? "Stop listening" : "Voice dictation"}
                  >
                    {isListening ? (
                      <Square className="w-3 h-3 fill-white" />
                    ) : (
                      <Mic className="w-3.5 h-3.5" />
                    )}
                  </button>

                  {/* Send button */}
                  <button
                    type="submit"
                    disabled={(!inputText.trim() && !attachment) || isLoading}
                    className={`w-8 h-8 rounded-full flex items-center justify-center transition-all duration-200 ${
                      inputText.trim() || attachment
                        ? "text-white shadow-md hover:scale-105 active:scale-95 cursor-pointer"
                        : "opacity-40 cursor-not-allowed text-slate-400"
                    }`}
                    style={
                      inputText.trim() || attachment
                        ? {
                            background: theme.gradient,
                            boxShadow: `0 2px 10px ${theme.glow}`,
                          }
                        : {}
                    }
                    title="Send message"
                  >
                    <Send className="w-3.5 h-3.5" />
                  </button>
                </div>
              }
            />
          </form>
        </div>
      </div>
    </div>
  );
};
