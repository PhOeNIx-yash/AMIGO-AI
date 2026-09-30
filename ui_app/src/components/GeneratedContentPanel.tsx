import React, { useState, useEffect, useRef } from "react";
import { motion } from "motion/react";
// Import optimized motion configs
import { fluidSpring, scaleFade } from "../utils/motionConfig";
import { Copy, Edit, RotateCcw, Download, Send, X, Check, Loader2 } from "lucide-react";

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
  contentType,
  topic,
  onInsert,
  onCopy,
  onRegenerate,
  onSave,
  onClose,
  isDark,
  colorTheme,
}: GeneratedContentPanelProps) {
  const [editedContent, setEditedContent] = useState(content);
  const [isEditing, setIsEditing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [inserting, setInserting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-focus textarea when editing starts
  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [isEditing]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(editedContent);
      setCopied(true);
      onCopy(editedContent);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Failed to copy:", err);
    }
  };

  const handleInsert = async () => {
    setInserting(true);
    try {
      await onInsert(editedContent);
    } finally {
      setInserting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(editedContent);
    } finally {
      setSaving(false);
    }
  };

  const handleRegenerate = async () => {
    setRegenerating(true);
    try {
      await onRegenerate();
    } finally {
      setRegenerating(false);
    }
  };

  const handleClose = () => {
    onClose();
  };

  const getContentTypeIcon = () => {
    switch (contentType.toLowerCase()) {
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
    return contentType.charAt(0).toUpperCase() + contentType.slice(1);
  };

  return (
    <motion.div
      variants={scaleFade}
      initial="hidden"
      animate="show"
      exit="exit"
      className="relative w-full max-w-3xl mx-auto bg-white/5 backdrop-blur-xl border border-white/10 rounded-2xl overflow-hidden shadow-2xl z-50 gpu-accelerated"
      style={{
        boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.5)",
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-white/10">
        <div className="flex items-center space-x-3">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center text-2xl"
            style={{ background: "linear-gradient(135deg, #8b5cf6, #ec4899)" }}
          >
            {getContentTypeIcon()}
          </div>
          <div>
            <h3 className="font-semibold text-white text-lg">{getContentTypeLabel()}</h3>
            <p className="text-xs text-white/60 truncate max-w-xs">{topic}</p>
          </div>
        </div>
        <motion.button
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.9 }}
          onClick={handleClose}
          className="p-2 rounded-lg text-white/50 hover:text-white hover:bg-white/10 transition-colors"
          aria-label="Close panel"
        >
          <X className="w-5 h-5" />
        </motion.button>
      </div>

      {/* Content Area */}
      <div className="p-4 max-h-[60vh] overflow-hidden">
        {isEditing ? (
          <textarea
            ref={textareaRef}
            value={editedContent}
            onChange={(e) => setEditedContent(e.target.value)}
            className="w-full h-[50vh] min-h-[300px] bg-white/5 border border-white/10 rounded-xl p-4 text-white placeholder-white/40 font-mono text-sm leading-relaxed resize-none focus:outline-none focus:ring-2 focus:ring-purple-500/50 focus:border-purple-500/50"
            placeholder="Edit your content here..."
            spellCheck={true}
          />
        ) : (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="w-full h-[50vh] min-h-[300px] max-h-[50vh] overflow-y-auto bg-white/5 border border-white/10 rounded-xl p-4 text-white/90 font-mono text-sm leading-relaxed whitespace-pre-wrap break-words"
          >
            {editedContent}
          </motion.div>
        )}
      </div>

      {/* Action Buttons */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 border-t border-white/10 bg-white/5">
        {/* Left side - Edit/Regenerate */}
        <div className="flex items-center space-x-2">
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => setIsEditing(!isEditing)}
            disabled={regenerating || inserting}
            className="flex items-center space-x-2 px-4 py-2 rounded-xl text-sm font-medium text-white/90 hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            style={{
              background: isEditing ? "linear-gradient(135deg, #22c55e, #16a34a)" : "linear-gradient(135deg, #8b5cf6, #7c3aed)",
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
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={handleRegenerate}
            disabled={regenerating || inserting || isEditing}
            className="flex items-center space-x-2 px-4 py-2 rounded-xl text-sm font-medium text-white/90 hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={handleCopy}
            disabled={inserting || regenerating}
            className="flex items-center space-x-2 px-4 py-2 rounded-xl text-sm font-medium text-white/90 hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ background: copied ? "linear-gradient(135deg, #22c55e, #16a34a)" : "linear-gradient(135deg, #6366f1, #4f46e5)" }}
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
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={handleInsert}
            disabled={inserting || regenerating || isEditing}
            className="flex items-center space-x-2 px-4 py-2 rounded-xl text-sm font-medium text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={handleSave}
            disabled={saving || inserting || regenerating}
            className="flex items-center space-x-2 px-4 py-2 rounded-xl text-sm font-medium text-white/90 hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ background: "linear-gradient(135deg, #06b6d4, #0891b2)" }}
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

      {/* Hint text */}
      <div className="px-4 pb-4 text-center">
        <p className="text-xs text-white/40">
          Review and edit the content above. Click <strong>Insert</strong> to paste into the active window (Ctrl+V), or <strong>Copy</strong> to copy to clipboard.
        </p>
      </div>
    </motion.div>
  );
}