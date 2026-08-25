import React, { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
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
} from "lucide-react";
import { HistoryEntry, ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { sfx } from "../utils/audio";

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

export const HistoryPanel: React.FC<HistoryPanelProps> = ({
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
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set());
  const dropdownRef = useRef<HTMLDivElement>(null);
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;

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

  const intentOptions = Array.from(new Set(history.map((item) => item.response.intent).filter(Boolean)));
  const filteredHistory = history.filter((item) => {
    const matchesSearch =
      item.prompt.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.response.displayTitle?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.response.intent?.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesSearch && (intentFilter === "all" || item.response.intent === intentFilter);
  }).sort((a, b) => Number(pinnedIds.has(b.id)) - Number(pinnedIds.has(a.id)));

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

  const getIntentIcon = (intent: string) => {
    switch (intent) {
      case "translate_email":
      case "send_message":
        return <MessageSquare className="w-3.5 h-3.5 text-blue-400" />;
      case "book_restaurant":
        return <Utensils className="w-3.5 h-3.5 text-emerald-400" />;
      case "map":
        return <MapPin className="w-3.5 h-3.5 text-rose-400" />;
      case "schedule":
        return <Calendar className="w-3.5 h-3.5 text-amber-400" />;
      default:
        return <Sparkles className="w-3.5 h-3.5 text-indigo-400" />;
    }
  };

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <motion.aside
        id="voice-history-sidebar"
        initial={{ x: "-100%", opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: "-100%", opacity: 0 }}
        transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
        className={`absolute top-0 left-0 bottom-0 z-40 w-80 sm:w-96 flex flex-col border-r shadow-2xl backdrop-blur-2xl transition-colors duration-300 ${
          isDark
            ? "bg-[#0c0c18]/95 border-white/10 text-white"
            : "bg-white/95 border-black/10 text-slate-900"
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3.5 border-b border-white/5 dark:border-white/10">
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

          <div className="flex items-center space-x-1">
            {history.length > 0 && (
              <button
                id="clear-all-history-btn"
                onClick={onClearHistory}
                className="p-1.5 rounded-lg opacity-60 hover:opacity-100 hover:bg-rose-500/10 hover:text-rose-400 transition-colors text-xs flex items-center space-x-1"
                title="Clear All History"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
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
          <div className="p-3 pb-1 border-b border-white/5 dark:border-white/10">
            <div
              className={`flex items-center space-x-2 px-3 py-1.5 rounded-lg text-xs border ${
                isDark
                  ? "bg-white/5 border-white/10 text-white"
                  : "bg-black/5 border-black/10 text-slate-900"
              }`}
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
                    ? "bg-white/[0.04] hover:bg-white/[0.08] border-white/10 text-slate-200"
                    : "bg-black/[0.04] hover:bg-black/[0.07] border-black/10 text-slate-800"
                } ${isDropdownOpen ? (isDark ? "border-indigo-500/60 ring-2 ring-indigo-500/25 bg-white/[0.07]" : "border-indigo-500 ring-2 ring-indigo-500/20 bg-black/[0.06]") : ""}`}
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
                        ? "bg-[#111022]/98 border-white/15 text-slate-200 shadow-black/80 ring-1 ring-white/10"
                        : "bg-white/98 border-black/10 text-slate-800 shadow-slate-400/40 ring-1 ring-black/5"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setIntentFilter("all");
                        setIsDropdownOpen(false);
                      }}
                      className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs transition-colors ${
                        intentFilter === "all"
                          ? isDark
                            ? "bg-indigo-500/20 text-indigo-300 font-semibold"
                            : "bg-indigo-50 text-indigo-700 font-semibold"
                          : isDark
                          ? "hover:bg-white/10 text-slate-300"
                          : "hover:bg-black/5 text-slate-700"
                      }`}
                    >
                      <span>All activity</span>
                      {intentFilter === "all" && <Check className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />}
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
                              ? isDark
                                ? "bg-indigo-500/20 text-indigo-300 font-semibold"
                                : "bg-indigo-50 text-indigo-700 font-semibold"
                              : isDark
                              ? "hover:bg-white/10 text-slate-300"
                              : "hover:bg-black/5 text-slate-700"
                          }`}
                        >
                          <span className="truncate">{intent.replaceAll("_", " ")}</span>
                          {isSelected && <Check className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />}
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
                <Clock className="w-6 h-6 text-indigo-400 opacity-80" />
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
                <motion.div
                  key={item.id}
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={`group relative rounded-xl p-3 border transition-all duration-200 cursor-pointer ${
                    isDark
                      ? isExpanded
                        ? "bg-white/[0.08] border-indigo-500/50 shadow-lg"
                        : "bg-white/[0.04] hover:bg-white/[0.07] border-white/10 hover:border-indigo-500/30"
                      : isExpanded
                      ? "bg-white border-indigo-400 shadow-md"
                      : "bg-white/80 hover:bg-white border-black/10 hover:border-indigo-300 shadow-sm"
                  }`}
                  onClick={() => toggleExpand(item.id)}
                >
                  {/* Top Row: Intent & Time */}
                  <div className="flex items-center justify-between text-[11px] mb-1.5">
                    <div className="flex items-center space-x-1.5">
                      {getIntentIcon(item.response.intent)}
                      <span className="capitalize opacity-75 font-mono">
                        {item.response.intent?.replace("_", " ") || "Command"}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      {item.response.metadata?.status && <span className={`text-[10px] ${item.response.metadata.status === "completed" ? "text-emerald-400" : "text-rose-400"}`}>{item.response.metadata.status}</span>}
                      {typeof item.response.metadata?.duration_ms === "number" && <span className="text-[10px] opacity-50">{Math.round(item.response.metadata.duration_ms)}ms</span>}
                      <span className="opacity-50 text-[10px]">{formatTime(item.timestamp)}</span>
                    </div>
                  </div>

                  {/* Prompt Text */}
                  <p className="text-xs font-semibold leading-snug">
                    "{item.prompt}"
                  </p>
                  <button
                    type="button"
                    aria-label={pinnedIds.has(item.id) ? "Unpin result" : "Pin result"}
                    onClick={(e) => {
                      e.stopPropagation();
                      setPinnedIds((current) => {
                        const next = new Set(current);
                        if (next.has(item.id)) next.delete(item.id); else next.add(item.id);
                        return next;
                      });
                    }}
                    className={`absolute right-2 top-8 rounded-md p-1 transition-colors ${pinnedIds.has(item.id) ? "text-amber-400" : "opacity-30 hover:opacity-80"}`}
                    title={pinnedIds.has(item.id) ? "Unpin result" : "Pin result"}
                  >
                    <Pin className="h-3 w-3" />
                  </button>

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
                        <div className="p-2.5 rounded-lg bg-black/20 dark:bg-white/5 border border-white/5 leading-relaxed text-[11px] opacity-90">
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
                            className="text-[10px] font-medium opacity-70 hover:opacity-100 flex items-center space-x-1 underline"
                          >
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
                </motion.div>
              );
            })
          )}
        </div>

        {/* Footer info */}
        <div className="p-3 border-t border-white/5 dark:border-white/10 text-center text-[10px] opacity-40">
          History auto-syncs with Amigo Memory
        </div>
      </motion.aside>
    </AnimatePresence>
  );
};
