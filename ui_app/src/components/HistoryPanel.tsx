import React, { useState, useRef, useEffect, type ComponentProps, type ReactNode } from "react";
import {
  motion,
  AnimatePresence,
  animate,
  useMotionTemplate,
  useMotionValue,
  useReducedMotion,
  useTransform,
  type Transition,
} from "motion/react";
import {
  History as HistoryIcon,
  Play,
  Trash2,
  Search,
  Clock,
  ChevronRight,
  ChevronDown,
  Check,
  MessageSquare,
  Utensils,
  MapPin,
  Calendar,
  Sparkles,
  X,
  Pin,
  Sun,
  Folder,
  Cpu,
  Music,
} from "lucide-react";
import { HistoryEntry, ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { sfx } from "../utils/audio";

function cn(...classes: (string | undefined | null | false)[]) {
  return classes.filter(Boolean).join(" ");
}

const HINGE = "3px 6px";
const LID_OPEN = -35;
const WALL_TOP = 6;
const WALL_TOP_OPEN = 13.5;
const WALL_BASE = 20;

const HOLD = { deleted: 1400, kept: 600 };

const EASE = [0.32, 0.72, 0, 1] as const;
const EASE_LID = [0.34, 1.1, 0.64, 1] as const;

const WIDTH = { duration: 0.62, ease: EASE } as const;
const LID = { duration: 0.6, ease: EASE_LID } as const;
const WALL = { duration: 0.56, ease: EASE } as const;
const IN = { duration: 0.44, ease: EASE, delay: 0.14 } as const;
const OUT = { duration: 0.3, ease: EASE } as const;
const TAP = { duration: 0.2, ease: EASE } as const;
const SWAP = { duration: 0.22, ease: EASE } as const;
const SETTLE = { duration: 0.45, ease: EASE } as const;
const PRESS = {
  type: "spring",
  stiffness: 520,
  damping: 18,
  mass: 0.5,
} as const;
const INSTANT = { duration: 0 } as const;

const SURFACE = "bg-slate-200/80 dark:bg-white/10 hover:bg-slate-300/80 dark:hover:bg-white/15";
const RECESS = "bg-slate-300 dark:bg-black/70 backdrop-blur-md";
const GLYPH = "text-slate-600 dark:text-slate-300";
const FOCUS = "outline-none focus-visible:ring-2 focus-visible:ring-rose-500/50";
const ACCENT = "#f43f5e";

const LIFT =
  "shadow-[0_0.5px_1px_rgba(0,0,0,0.05),0_1px_3px_rgba(0,0,0,0.08),inset_0_0.5px_0_rgba(255,255,255,0.9)] dark:shadow-[0_0.5px_1px_rgba(0,0,0,0.35),0_1.5px_4px_rgba(0,0,0,0.25),inset_0_0.5px_0_rgba(255,255,255,0.07)]";

const ICON = {
  viewBox: "0 0 24 24",
  fill: "none",
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
} as const;

const panelMotion = {
  hidden: { opacity: 0, x: -6, transition: OUT },
  shown: { opacity: 1, x: 0, transition: { ...IN, staggerChildren: 0.07 } },
};

const circleMotion = {
  hidden: { opacity: 0, scale: 0.9, transition: OUT },
  shown: { opacity: 1, scale: 1, transition: IN },
};

function Circle({
  label,
  onClick,
  size = "default",
  children,
}: {
  label: string;
  onClick: () => void;
  size?: "sm" | "default";
  children: ReactNode;
}) {
  const reduced = useReducedMotion() ?? false;
  const isSm = size === "sm";

  return (
    <motion.div className="flex" variants={reduced ? undefined : circleMotion}>
      <motion.button
        type="button"
        aria-label={label}
        onClick={onClick}
        whileHover={reduced ? undefined : { scale: 1.05 }}
        whileTap={reduced ? undefined : { scale: 0.85 }}
        transition={PRESS}
        className={cn(
          "grid place-items-center rounded-full transition-colors duration-200 hover:bg-white dark:hover:bg-white/20",
          isSm ? "h-6 w-6" : "h-7 w-7",
          FOCUS,
          SURFACE,
          LIFT
        )}
      >
        <svg
          {...ICON}
          width={isSm ? "12" : "14"}
          height={isSm ? "12" : "14"}
          stroke="currentColor"
          strokeWidth="3"
        >
          {children}
        </svg>
      </motion.button>
    </motion.div>
  );
}

type DeleteStatus = "idle" | "deleted" | "kept";

export type DeleteButtonProps = Omit<
  ComponentProps<"div">,
  "onAnimationStart" | "onDrag" | "onDragStart" | "onDragEnd"
> & {
  size?: "sm" | "default";
  onConfirm?: () => void;
  onCancel?: () => void;
  disabled?: boolean;
};

export function DeleteButton({
  className,
  size = "default",
  onConfirm,
  onCancel,
  disabled = false,
  ...props
}: DeleteButtonProps) {
  const reduced = useReducedMotion() ?? false;
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<DeleteStatus>("idle");
  const trigger = useRef<HTMLButtonElement>(null);
  const confirmTimerRef = useRef<any>(null);
  const timing = (transition: Transition) => (reduced ? INSTANT : transition);

  useEffect(() => {
    return () => {
      if (confirmTimerRef.current) {
        clearTimeout(confirmTimerRef.current);
        confirmTimerRef.current = null;
      }
    };
  }, []);

  const isSm = size === "sm";
  const TILE = isSm ? 34 : 48;
  const PANEL = isSm ? 66 : 84;
  const iconSize = isSm ? 15 : 20;

  const top = useMotionValue(WALL_TOP);
  const wall = useTransform(top, (y) => WALL_BASE - y);
  const bin = useMotionTemplate`M19 ${top}v${wall}a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V${top}`;
  const settle = useMotionValue(1);

  useEffect(() => {
    if (!open) return;
    const walls = animate(
      top,
      WALL_TOP_OPEN,
      reduced ? INSTANT : WALL,
    );
    return () => walls.stop();
  }, [open, reduced, top]);

  useEffect(() => {
    if (status === "idle") return;
    const nudge =
      status === "kept" && !reduced
        ? animate(settle, [1, 0.86, 1], SETTLE)
        : null;
    const done = setTimeout(() => setStatus("idle"), HOLD[status]);
    return () => {
      nudge?.stop();
      clearTimeout(done);
    };
  }, [status, reduced, settle]);

  const resolve = (next: Exclude<DeleteStatus, "idle">) => {
    setOpen(false);
    setStatus(next);
    trigger.current?.focus();
    if (next === "deleted") {
      onConfirm?.();
    } else {
      onCancel?.();
    }
  };

  return (
    <motion.div
      data-slot="delete-button"
      data-state={open ? "open" : "closed"}
      data-status={status}
      className={cn(
        "relative rounded-xl border border-transparent overflow-hidden transition-colors",
        isSm ? "h-[34px]" : "h-12",
        SURFACE,
        GLYPH,
        disabled && "opacity-40 pointer-events-none",
        className
      )}
      animate={{ width: open ? TILE + PANEL : TILE }}
      transition={timing(WIDTH)}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) resolve("kept");
      }}
      {...props}
    >
      <motion.button
        ref={trigger}
        type="button"
        disabled={disabled}
        aria-label="Delete"
        aria-expanded={open}
        onClick={() => {
          if (disabled) return;
          if (open) return resolve("kept");
          setStatus("idle");
          setOpen(true);
        }}
        whileTap={reduced || disabled ? undefined : { scale: 0.94 }}
        transition={TAP}
        className={cn(
          "relative z-10 grid place-items-center rounded-xl",
          isSm ? "h-[34px] w-[34px]" : "h-12 w-12",
          FOCUS,
        )}
      >
        <AnimatePresence mode="wait" initial={false}>
          {status === "deleted" ? (
            <motion.svg
              key="done"
              {...ICON}
              width={iconSize}
              height={iconSize}
              stroke={ACCENT}
              strokeWidth="2.5"
              initial={{ opacity: 0, scale: 0.6 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.6 }}
              transition={timing(SWAP)}
            >
              <motion.path
                d="M4 12.5 9.5 18 20 7"
                initial={reduced ? undefined : { pathLength: 0 }}
                animate={reduced ? undefined : { pathLength: 1 }}
                transition={SETTLE}
              />
            </motion.svg>
          ) : (
            <motion.svg
              key="bin"
              {...ICON}
              width={iconSize}
              height={iconSize}
              stroke="currentColor"
              strokeWidth="2"
              className="overflow-visible"
              style={{ scale: settle }}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={timing(SWAP)}
            >
              <motion.path d={bin} />
              <motion.g
                style={{ transformBox: "view-box", transformOrigin: HINGE }}
                animate={{ rotate: open ? LID_OPEN : 0 }}
                transition={timing(LID)}
              >
                <path d="M3 6h18" />
                <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </motion.g>
            </motion.svg>
          )}
        </AnimatePresence>
      </motion.button>

      <span role="status" aria-live="polite" className="sr-only">
        {status === "deleted" ? "Deleted" : status === "kept" ? "Kept" : ""}
      </span>

      <AnimatePresence>
        {open && (
          <motion.div
            key="panel"
            style={{ width: PANEL }}
            className={cn(
              "absolute inset-y-0 right-0 flex items-center justify-center gap-1.5 rounded-xl border border-white/10",
              RECESS,
            )}
            variants={reduced ? undefined : panelMotion}
            initial="hidden"
            animate="shown"
            exit="hidden"
          >
            <span
              aria-hidden
              className={cn(
                "absolute -left-1.25 top-1/2 z-20 h-2.5 w-1.5 -translate-y-1/2 [clip-path:polygon(100%_0,0_50%,100%_100%)]",
                RECESS,
              )}
            />
            <Circle label="Confirm delete" size={size} onClick={() => resolve("deleted")}>
              <path d="M4 12.5 9.5 18 20 7" stroke={ACCENT} />
            </Circle>
            <Circle label="Cancel" size={size} onClick={() => resolve("kept")}>
              <path d="M6 6 18 18M18 6 6 18" />
            </Circle>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

interface HistoryPanelProps {
  isOpen: boolean;
  onClose: () => void;
  history: HistoryEntry[];
  onReRunCommand: (prompt: string) => void;
  onSelectHistoryEntry: (entry: HistoryEntry) => void;
  onClearHistory: () => void;
  isDark: boolean;
  colorTheme?: ColorTheme;
}

const HistoryPanelComponent: React.FC<HistoryPanelProps> = ({
  isOpen,
  onClose,
  history,
  onReRunCommand,
  onSelectHistoryEntry,
  onClearHistory,
  isDark,
  colorTheme = "violet",
}) => {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [intentFilter, setIntentFilter] = useState("all");
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem("amigo_pinned_history_ids");
      return saved ? new Set(JSON.parse(saved)) : new Set();
    } catch {
      return new Set();
    }
  });
  const dropdownRef = useRef<HTMLDivElement>(null);
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;

  const togglePin = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setPinnedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      try {
        localStorage.setItem("amigo_pinned_history_ids", JSON.stringify(Array.from(next)));
      } catch {}
      return next;
    });
  };

  // Close dropdown on click outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    };
    if (isDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isDropdownOpen]);

  const toggleExpand = (id: string) => {
    sfx.playClick();
    setExpandedId((prev) => (prev === id ? null : id));
  };

  const intentOptions = React.useMemo(() => {
    return Array.from(new Set(history.map((item) => item.response.intent).filter(Boolean))) as string[];
  }, [history]);

  const filteredHistory = React.useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    return history
      .filter((item) => {
        const matchesSearch =
          !q ||
          item.prompt.toLowerCase().includes(q) ||
          item.response.displayTitle?.toLowerCase().includes(q) ||
          item.response.intent?.toLowerCase().includes(q);
        return matchesSearch && (intentFilter === "all" || item.response.intent === intentFilter);
      })
      .sort((a, b) => Number(pinnedIds.has(b.id)) - Number(pinnedIds.has(a.id)));
  }, [history, searchQuery, intentFilter, pinnedIds]);

  const formatTime = (timestamp: number | string | undefined) => {
    if (!timestamp) return "Just now";
    let timeMs: number;
    if (typeof timestamp === "number") {
      timeMs = timestamp;
    } else {
      const parsed = new Date(timestamp).getTime();
      timeMs = isNaN(parsed) ? Date.now() : parsed;
    }

    const diffMs = Math.max(0, Date.now() - timeMs);
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);

    if (diffSec < 45) return "Just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    return new Date(timeMs).toLocaleDateString([], { month: "short", day: "numeric" });
  };

  const formatPromptDisplay = (rawPrompt: string): string => {
    if (!rawPrompt) return "";
    const match = rawPrompt.match(/^\[Attached (?:Document|Image|File):\s*([^\]\n]+)\]/);
    if (match) {
      const filename = match[1].trim();
      const qMatch = rawPrompt.match(/User Question \/ Task:\s*(.+)$/s);
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
    return rawPrompt;
  };

  const getIntentIcon = (intent?: string) => {
    const clean = (intent || "").toLowerCase();
    if (clean.includes("weather")) return <Sun className="w-3.5 h-3.5 text-amber-400" />;
    if (clean.includes("timer") || clean.includes("stopwatch") || clean.includes("clock")) return <Clock className="w-3.5 h-3.5 text-amber-400" />;
    if (clean.includes("file") || clean.includes("folder")) return <Folder className="w-3.5 h-3.5 text-indigo-400" />;
    if (clean.includes("youtube") || clean.includes("music") || clean.includes("media")) return <Music className="w-3.5 h-3.5 text-rose-400" />;
    if (clean.includes("search") || clean.includes("google") || clean.includes("web")) return <Search className="w-3.5 h-3.5 text-sky-400" />;
    if (clean.includes("email") || clean.includes("mail") || clean.includes("message")) return <MessageSquare className="w-3.5 h-3.5 text-blue-400" />;
    if (clean.includes("restaurant") || clean.includes("food")) return <Utensils className="w-3.5 h-3.5 text-emerald-400" />;
    if (clean.includes("map") || clean.includes("location")) return <MapPin className="w-3.5 h-3.5 text-rose-400" />;
    if (clean.includes("calendar") || clean.includes("schedule")) return <Calendar className="w-3.5 h-3.5 text-purple-400" />;
    if (clean.includes("system") || clean.includes("device") || clean.includes("stats") || clean.includes("hardware")) return <Cpu className="w-3.5 h-3.5 text-amber-400" />;
    return <Sparkles className="w-3.5 h-3.5" style={{ color: theme.accent || theme.primary }} />;
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.aside
          id="voice-history-sidebar"
          initial={{ x: "-100%" }}
          animate={{ x: 0 }}
          exit={{ x: "-100%" }}
          transition={{ duration: 0.2, ease: "easeOut" }}
          className={`absolute top-0 left-0 bottom-0 z-40 w-80 sm:w-96 flex flex-col border-r shadow-2xl transition-colors duration-150 antialiased transform-gpu [contain:content] ${
            isDark
              ? "border-white/10 text-slate-100"
              : "border-black/10 text-slate-900"
          }`}
          style={{
            background: isDark
              ? `radial-gradient(ellipse 120% 70% at 0% 0%, ${theme.primary}15 0%, #0c0c18 65%)`
              : `radial-gradient(ellipse 120% 70% at 0% 0%, ${theme.primary}08 0%, #ffffff 65%)`,
            borderColor: isDark ? `${theme.primary}22` : `${theme.primary}15`,
          }}
        >
        {/* Header */}
        <div
          className="flex items-center justify-between px-4 py-3.5 border-b"
          style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
        >
          <div className="flex items-center space-x-2">
            <div
              className="p-1.5 rounded-lg text-white"
              style={{ background: theme.gradient }}
            >
              <HistoryIcon className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-xs font-semibold tracking-wide uppercase">
                Activity History
              </h2>
              <p className="text-[10px] opacity-50">
                {history.length} logged command{history.length === 1 ? "" : "s"}
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-1.5">
            <DeleteButton
              size="sm"
              onConfirm={onClearHistory}
              disabled={history.length === 0}
              title="Clear All History"
            />
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg opacity-60 hover:opacity-100 hover:bg-white/10 transition-colors"
              title="Close Panel"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Search Filter Bar */}
        {history.length > 0 && (
          <div
            className="p-3 pb-1 border-b"
            style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
          >
            <div
              className={`flex items-center space-x-2 px-3 py-1.5 rounded-lg text-xs border ${
                isDark
                  ? "bg-white/5 text-white"
                  : "bg-black/5 text-slate-900"
              }`}
              style={{ borderColor: isDark ? `${theme.primary}22` : `${theme.primary}18` }}
            >
              <Search className="w-3.5 h-3.5 opacity-40 flex-shrink-0" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search past commands..."
                className="w-full bg-transparent focus:outline-none placeholder:opacity-40"
              />
              {searchQuery && (
                <button onClick={() => setSearchQuery("")} className="opacity-40 hover:opacity-100">
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
            {/* Custom Fluent Filter Dropdown */}
            <div className="relative mt-2" ref={dropdownRef}>
              <button
                type="button"
                id="history-intent-filter-btn"
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs font-medium border transition-all duration-200 ${
                  isDark
                    ? "bg-white/[0.04] hover:bg-white/[0.08] text-slate-200"
                    : "bg-black/[0.04] hover:bg-black/[0.07] text-slate-800"
                }`}
                style={
                  isDropdownOpen
                    ? {
                        borderColor: `${theme.primary}70`,
                        boxShadow: `0 0 0 2px ${theme.primary}25`,
                        backgroundColor: isDark ? `${theme.primary}10` : `${theme.primary}08`,
                      }
                    : { borderColor: isDark ? `${theme.primary}20` : `${theme.primary}15` }
                }
              >
                <div className="flex items-center space-x-2 truncate">
                  <span className="opacity-50 text-[11px]">Filter:</span>
                  <span className="capitalize font-semibold text-xs">
                    {intentFilter === "all" ? "All activity" : intentFilter.replaceAll("_", " ")}
                  </span>
                </div>
                <ChevronDown
                  className={`w-3.5 h-3.5 opacity-60 transition-transform duration-200 flex-shrink-0 ${
                    isDropdownOpen ? "rotate-180" : ""
                  }`}
                />
              </button>

              {/* Floating Glassmorphic Dropdown Menu */}
              <AnimatePresence>
                {isDropdownOpen && (
                  <motion.div
                    initial={{ opacity: 0, y: -6, scale: 0.97 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: -6, scale: 0.97 }}
                    transition={{ duration: 0.15, ease: "easeOut" }}
                    className={`absolute left-0 right-0 top-full mt-1.5 z-50 rounded-xl p-1.5 shadow-2xl border backdrop-blur-2xl max-h-56 overflow-y-auto ${
                      isDark
                        ? "bg-[#111022]/98 text-slate-200 shadow-black/80 ring-1 ring-white/10"
                        : "bg-white/98 text-slate-800 shadow-slate-400/40 ring-1 ring-black/5"
                    }`}
                    style={{ borderColor: isDark ? `${theme.primary}30` : `${theme.primary}20` }}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setIntentFilter("all");
                        setIsDropdownOpen(false);
                      }}
                      className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs transition-colors ${
                        intentFilter === "all"
                          ? "font-semibold"
                          : isDark
                          ? "hover:bg-white/10 text-slate-300"
                          : "hover:bg-black/5 text-slate-700"
                      }`}
                      style={
                        intentFilter === "all"
                          ? {
                              backgroundColor: isDark ? `${theme.primary}22` : `${theme.primary}12`,
                              color: isDark ? (theme.accent || theme.primary) : theme.primary,
                            }
                          : {}
                      }
                    >
                      <span>All activity</span>
                      {intentFilter === "all" && (
                        <Check className="w-3.5 h-3.5 flex-shrink-0" style={{ color: theme.accent || theme.primary }} />
                      )}
                    </button>

                    {intentOptions.map((intent) => {
                      const isSelected = intentFilter === intent;
                      return (
                        <button
                          key={intent}
                          type="button"
                          onClick={() => {
                            setIntentFilter(intent);
                            setIsDropdownOpen(false);
                          }}
                          className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs capitalize transition-colors ${
                            isSelected
                              ? "font-semibold"
                              : isDark
                              ? "hover:bg-white/10 text-slate-300"
                              : "hover:bg-black/5 text-slate-700"
                          }`}
                          style={
                            isSelected
                              ? {
                                  backgroundColor: isDark ? `${theme.primary}22` : `${theme.primary}12`,
                                  color: isDark ? (theme.accent || theme.primary) : theme.primary,
                                }
                              : {}
                          }
                        >
                          <span className="truncate">{intent.replaceAll("_", " ")}</span>
                          {isSelected && (
                            <Check className="w-3.5 h-3.5 flex-shrink-0" style={{ color: theme.accent || theme.primary }} />
                          )}
                        </button>
                      );
                    })}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        )}

        {/* Scrollable History List */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2.5 no-scrollbar">
          {filteredHistory.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center p-6 space-y-3 opacity-60">
              <div className="p-3 rounded-full bg-white/5">
                <Clock className="w-6 h-6 opacity-80" style={{ color: theme.accent || theme.primary }} />
              </div>
              <div>
                <p className="text-xs font-medium">No commands found</p>
                <p className="text-[11px] opacity-70 mt-1 max-w-[200px]">
                  {searchQuery
                    ? "No past voice commands match your search query."
                    : "Speak or enter a command using the microphone below to build your history."}
                </p>
              </div>
            </div>
          ) : (
            filteredHistory.map((item) => {
              const isExpanded = expandedId === item.id;
              const detailsText =
                item.response.executionSummary?.details ||
                item.response.executionSummary?.secondaryDetails ||
                item.response.speechReply ||
                "";

              return (
                <div
                  key={item.id}
                  className={`group relative rounded-xl p-3 border transition-colors duration-150 cursor-pointer ${
                    isDark
                      ? isExpanded
                        ? "bg-white/[0.08] shadow-lg"
                        : "bg-white/[0.04] hover:bg-white/[0.07]"
                      : isExpanded
                      ? "bg-white shadow-md"
                      : "bg-white/80 hover:bg-white shadow-sm"
                  }`}
                  style={{
                    borderColor: isExpanded
                      ? `${theme.primary}60`
                      : isDark
                      ? `${theme.primary}20`
                      : `${theme.primary}18`,
                    boxShadow: isExpanded ? `0 4px 20px -2px ${theme.primary}20` : undefined,
                  }}
                  onClick={() => {
                    toggleExpand(item.id);
                  }}
                >
                  {/* Top Row: Intent, Time & Pin */}
                  <div className="flex items-center justify-between text-[11px] mb-1.5">
                    <div className="flex items-center space-x-1.5 min-w-0 pr-1">
                      {getIntentIcon(item.response.intent)}
                      <span className="capitalize opacity-75 font-mono truncate">
                        {item.response.intent?.replace(/_/g, " ") || "Command"}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      {item.response.metadata?.status && (
                        <span className={`text-[10px] ${item.response.metadata.status === "completed" ? "text-emerald-400" : "text-rose-400"}`}>
                          {item.response.metadata.status}
                        </span>
                      )}
                      {typeof item.response.metadata?.duration_ms === "number" && (
                        <span className="text-[10px] opacity-50">{Math.round(item.response.metadata.duration_ms)}ms</span>
                      )}
                      <span className="opacity-50 text-[10px]">{formatTime(item.timestamp)}</span>
                      <button
                        type="button"
                        aria-label={pinnedIds.has(item.id) ? "Unpin result" : "Pin result"}
                        onClick={(e) => togglePin(item.id, e)}
                        className={`rounded-md p-0.5 transition-colors ${
                          pinnedIds.has(item.id) ? "text-amber-400" : "opacity-30 hover:opacity-80"
                        }`}
                        title={pinnedIds.has(item.id) ? "Unpin result" : "Pin result"}
                      >
                        <Pin className="h-3 w-3" />
                      </button>
                    </div>
                  </div>

                  {/* Prompt Text with Line Clamp */}
                  <p className={`text-xs font-semibold leading-snug ${isExpanded ? "" : "line-clamp-2"}`}>
                    "{formatPromptDisplay(item.prompt)}"
                  </p>

                  {/* Brief snippet when collapsed */}
                  {!isExpanded && detailsText && (
                    <p className="text-[11px] opacity-60 mt-1 line-clamp-1">
                      {detailsText}
                    </p>
                  )}

                  {/* Expanded Full Details Accordion */}
                  <AnimatePresence>
                    {isExpanded && (
                      <motion.div
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: "auto" }}
                        exit={{ opacity: 0, height: 0 }}
                        transition={{ duration: 0.2 }}
                        className="mt-2.5 pt-2.5 border-t border-white/10 text-xs space-y-2"
                      >
                        <div
                          className="p-2.5 rounded-lg bg-black/20 dark:bg-white/5 border leading-relaxed text-[11px] opacity-90"
                          style={{ borderColor: isDark ? `${theme.primary}20` : `${theme.primary}15` }}
                        >
                          {detailsText || "Command executed successfully."}
                        </div>

                        <div className="flex items-center justify-between pt-1">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              sfx.playClick();
                              onSelectHistoryEntry(item);
                            }}
                            className="flex items-center space-x-1 px-2 py-1 rounded-md text-white transition-all text-[10px] font-medium shadow-sm active:scale-95"
                            style={{ background: theme.gradient }}
                          >
                            <Sparkles className="w-2.5 h-2.5" />
                            <span>Open on stage</span>
                          </button>

                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              sfx.playClick();
                              onReRunCommand(item.prompt);
                            }}
                            className="flex items-center space-x-1 px-2.5 py-1 rounded-md text-white transition-all text-[10px] font-medium shadow-sm active:scale-95"
                            style={{ background: theme.gradient }}
                            title="Re-execute this command"
                          >
                            <Play className="w-2.5 h-2.5 fill-current" />
                            <span>Re-run</span>
                          </button>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {/* Toggle Indicator */}
                  {!isExpanded && (
                    <div className="flex items-center justify-between mt-2 pt-1.5 border-t border-white/5">
                      <span className="text-[10px] opacity-50 flex items-center space-x-1">
                        <span>Tap to view details</span>
                      </span>
                      <ChevronRight className="w-3 h-3 opacity-40 group-hover:opacity-80 transition-opacity" />
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Footer info */}
        <div
          className="p-3 border-t text-center text-[10px] opacity-40"
          style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
        >
          History auto-syncs with Amigo Memory
        </div>
      </motion.aside>
      )}
    </AnimatePresence>
  );
};

export const HistoryPanel = React.memo(HistoryPanelComponent);
export default HistoryPanel;
