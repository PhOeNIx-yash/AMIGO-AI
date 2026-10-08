import React, { useState, useEffect, useRef } from "react";
import { motion } from "motion/react";
import { scaleFade } from "../utils/motionConfig";
import { Copy, Edit, RotateCcw, Download, Send, X, Check, Loader2, AlertCircle } from "lucide-react";
import { COLOR_THEMES } from "../data/presets";

interface GeneratedContentPanelProps {
  content: string;
  contentType: string;
  topic: string;
  onInsert: (content: string) => void;
  onCopy: (content: string) => void;
  onRegenerate: () => void;
  onSave: (content: string) => void;
  onClose: () => void;
  isDark: boolean;
  colorTheme: string;
}

export function GeneratedContentPanel({
  content,
  contentType = "document",
  topic,
  onInsert,
  onCopy,
  onRegenerate,
  onSave,
  onClose,
  isDark,
  colorTheme,
}: GeneratedContentPanelProps) {
  const [editedContent, setEditedContent] = useState(content || "");
  const [isEditing, setIsEditing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [inserting, setInserting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const theme = COLOR_THEMES[colorTheme as keyof typeof COLOR_THEMES] || COLOR_THEMES.beams || COLOR_THEMES.violet;

  // Synchronize editedContent whenever new content is passed from parent (e.g. after regeneration)
  useEffect(() => {
    setEditedContent(content || "");
  }, [content]);

  // Auto-focus textarea when editing starts
  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [isEditing]);

  // Escape key handler for dialog dismissal
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (isEditing) {
          setIsEditing(false);
        } else {
          onClose();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isEditing, onClose]);

  const handleCopy = async () => {
    setActionError(null);
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(editedContent);
      } else {
        // Fallback for non-secure contexts
        const textarea = document.createElement("textarea");
        textarea.value = editedContent;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
      }
      setCopied(true);
      onCopy(editedContent);
      setTimeout(() => setCopied(false), 2000);
    } catch (err: any) {
      console.warn("Failed to copy:", err);
      setActionError("Could not copy to clipboard");
    }
  };

  const handleInsert = async () => {
    setInserting(true);
    setActionError(null);
    try {
      await onInsert(editedContent);
    } catch (err: any) {
      console.warn("Failed to insert:", err);
      setActionError("Failed to insert text into active app");
    } finally {
      setInserting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setActionError(null);
    try {
      await onSave(editedContent);
    } catch (err: any) {
      console.warn("Failed to save:", err);
      setActionError("Failed to save content");
    } finally {
      setSaving(false);
    }
  };

  const handleRegenerate = async () => {
    setRegenerating(true);
    setActionError(null);
    try {
      await onRegenerate();
    } catch (err: any) {
      console.warn("Failed to regenerate:", err);
      setActionError("Failed to regenerate content");
    } finally {
      setRegenerating(false);
    }
  };

  const safeType = (contentType || "document").toLowerCase();
  const isCode = safeType === "code";

  const getContentTypeIcon = () => {
    switch (safeType) {
      case "email":
        return "📧";
      case "letter":
        return "📄";
      case "application":
        return "📝";
      case "code":
        return "💻";
      case "document":
        return "📋";
      case "message":
        return "💬";
      default:
        return "📄";
    }
  };

  const getContentTypeLabel = () => {
    return safeType.charAt(0).toUpperCase() + safeType.slice(1);
  };

  return (
    <motion.div
      variants={scaleFade}
      initial="hidden"
      animate="show"
      exit="exit"
      role="dialog"
      aria-modal="true"
      aria-labelledby="generated-content-title"
      className={`relative w-full max-w-3xl mx-auto rounded-2xl overflow-hidden shadow-2xl z-50 gpu-accelerated border flex flex-col max-h-[90vh] sm:max-h-[85vh] ${
        isDark
          ? "bg-slate-900/95 text-white border-white/15"
          : "bg-white/98 text-slate-900 border-slate-200"
      }`}
      style={{
        boxShadow: isDark
          ? "0 25px 50px -12px rgba(0, 0, 0, 0.7), 0 0 30px rgba(0, 0, 0, 0.5)"
          : "0 20px 40px -10px rgba(0, 0, 0, 0.15)",
      }}
    >
      {/* Header */}
      <div
        className={`flex items-center justify-between p-4 border-b ${
          isDark ? "border-white/10" : "border-slate-200"
        }`}
      >
        <div className="flex items-center space-x-3 min-w-0">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center text-2xl flex-shrink-0 shadow-md"
            style={{ background: theme.gradient || "linear-gradient(135deg, #8b5cf6, #ec4899)" }}
          >
            {getContentTypeIcon()}
          </div>
          <div className="min-w-0 flex-1">
            <h3
              id="generated-content-title"
              className="font-semibold text-base sm:text-lg truncate"
            >
              {getContentTypeLabel()}
            </h3>
            <p className={`text-xs truncate ${isDark ? "text-slate-400" : "text-slate-500"}`}>
              {topic || "Generated Content"}
            </p>
          </div>
        </div>
        <motion.button
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.9 }}
          onClick={onClose}
          className={`p-2 rounded-xl transition-colors ${
            isDark
              ? "text-slate-400 hover:text-white hover:bg-white/10"
              : "text-slate-500 hover:text-slate-900 hover:bg-slate-100"
          }`}
          aria-label="Close panel"
        >
          <X className="w-5 h-5" />
        </motion.button>
      </div>

      {/* Content Area */}
      <div className="p-4 flex-1 overflow-y-auto">
        {actionError && (
          <div className="mb-3 px-3 py-2 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-400 text-xs flex items-center space-x-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>{actionError}</span>
          </div>
        )}

        {isEditing ? (
          <textarea
            ref={textareaRef}
            value={editedContent}
            onChange={(e) => setEditedContent(e.target.value)}
            className={`w-full h-full min-h-[220px] max-h-[50vh] rounded-xl p-4 text-sm leading-relaxed resize-none focus:outline-none border transition-colors ${
              isCode ? "font-mono" : "font-sans"
            } ${
              isDark
                ? "bg-black/30 border-white/15 text-white placeholder-white/40 focus:border-indigo-400"
                : "bg-slate-50 border-slate-300 text-slate-900 placeholder-slate-400 focus:border-indigo-500"
            }`}
            placeholder="Edit your content here..."
            spellCheck={true}
          />
        ) : (
          <div
            className={`w-full min-h-[220px] max-h-[50vh] overflow-y-auto rounded-xl p-4 text-sm leading-relaxed whitespace-pre-wrap break-words border ${
              isCode ? "font-mono" : "font-sans"
            } ${
              isDark
                ? "bg-black/20 border-white/10 text-slate-100"
                : "bg-slate-50 border-slate-200 text-slate-800"
            }`}
          >
            {editedContent}
          </div>
        )}
      </div>

      {/* Action Buttons */}
      <div
        className={`flex flex-wrap items-center justify-between gap-2.5 p-3 sm:p-4 border-t ${
          isDark ? "border-white/10 bg-black/20" : "border-slate-200 bg-slate-50"
        }`}
      >
        {/* Left side - Edit/Regenerate */}
        <div className="flex items-center space-x-2">
          <motion.button
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={() => {
              if (isEditing) {
                // Done editing: persist changes
                onSave?.(editedContent);
              }
              setIsEditing(!isEditing);
            }}
            disabled={regenerating || inserting}
            className="flex items-center space-x-1.5 px-3 sm:px-4 py-2 rounded-xl text-xs sm:text-sm font-semibold text-white transition-opacity disabled:opacity-50"
            style={{
              background: isEditing
                ? "linear-gradient(135deg, #10b981, #059669)"
                : "linear-gradient(135deg, #6366f1, #4f46e5)",
            }}
          >
            {isEditing ? (
              <>
                <Check className="w-4 h-4" />
                <span>Done Editing</span>
              </>
            ) : (
              <>
                <Edit className="w-4 h-4" />
                <span>Edit</span>
              </>
            )}
          </motion.button>

          <motion.button
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={handleRegenerate}
            disabled={regenerating || inserting || isEditing}
            className="flex items-center space-x-1.5 px-3 sm:px-4 py-2 rounded-xl text-xs sm:text-sm font-semibold text-white transition-opacity disabled:opacity-50"
            style={{ background: "linear-gradient(135deg, #f59e0b, #d97706)" }}
          >
            {regenerating ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Regenerating...</span>
              </>
            ) : (
              <>
                <RotateCcw className="w-4 h-4" />
                <span>Regenerate</span>
              </>
            )}
          </motion.button>
        </div>

        {/* Right side - Copy/Insert/Save */}
        <div className="flex items-center space-x-2">
          <motion.button
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={handleCopy}
            disabled={inserting || regenerating}
            className="flex items-center space-x-1.5 px-3 sm:px-4 py-2 rounded-xl text-xs sm:text-sm font-semibold text-white transition-opacity disabled:opacity-50"
            style={{
              background: copied
                ? "linear-gradient(135deg, #10b981, #059669)"
                : "linear-gradient(135deg, #0284c7, #0369a1)",
            }}
          >
            {copied ? (
              <>
                <Check className="w-4 h-4" />
                <span>Copied!</span>
              </>
            ) : (
              <>
                <Copy className="w-4 h-4" />
                <span>Copy</span>
              </>
            )}
          </motion.button>

          <motion.button
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={handleInsert}
            disabled={inserting || regenerating || isEditing}
            className="flex items-center space-x-1.5 px-3 sm:px-4 py-2 rounded-xl text-xs sm:text-sm font-semibold text-white transition-opacity disabled:opacity-50"
            style={{ background: "linear-gradient(135deg, #ec4899, #db2777)" }}
          >
            {inserting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Inserting...</span>
              </>
            ) : (
              <>
                <Send className="w-4 h-4" />
                <span>Insert</span>
              </>
            )}
          </motion.button>

          <motion.button
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={handleSave}
            disabled={saving || inserting || regenerating}
            className="flex items-center space-x-1.5 px-3 sm:px-4 py-2 rounded-xl text-xs sm:text-sm font-semibold text-white transition-opacity disabled:opacity-50"
            style={{ background: "linear-gradient(135deg, #0d9488, #0f766e)" }}
          >
            {saving ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Saving...</span>
              </>
            ) : (
              <>
                <Download className="w-4 h-4" />
                <span>Save</span>
              </>
            )}
          </motion.button>
        </div>
      </div>
    </motion.div>
  );
}