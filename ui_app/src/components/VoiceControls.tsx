import React, { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Mic,
  Send,
  Loader2,
  Square,
  Radio,
  Sparkles,
  Plus,
  FileText,
  Image as ImageIcon,
  Folder,
  X,
  CheckCircle2,
  AlertCircle,
  FileCode,
} from "lucide-react";
import { sfx } from "../utils/audio";
import { transcribeAudio, uploadFileToBackend } from "../services/assistantApi";
import { BackendConfig, ColorTheme, AttachmentItem } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { audioBus } from "../utils/audioBus";

interface VoiceControlsProps {
  onProcessCommand: (prompt: string, attachment?: AttachmentItem) => void;
  isListening: boolean;
  onSetListening: (listening: boolean) => void;
  onAudioLevelChange?: (level: number) => void;
  onTranscriptChange?: (text: string) => void;
  isDark: boolean;
  colorTheme?: ColorTheme;
  disabled?: boolean;
  backendConfig?: BackendConfig;
}

export const VoiceControls: React.FC<VoiceControlsProps> = ({
  onProcessCommand,
  isListening,
  onSetListening,
  onAudioLevelChange,
  onTranscriptChange,
  isDark,
  colorTheme = "violet",
  disabled = false,
  backendConfig,
}) => {
  const [inputText, setInputText] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState("");
  const liveTranscriptRef = useRef("");
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;

  // File attachment & popover state
  const [attachment, setAttachment] = useState<AttachmentItem | null>(null);
  const [isMenuOpen, setIsMenuOpen] = useState<boolean>(false);
  const [isDraggingOver, setIsDraggingOver] = useState<boolean>(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const fileInputDocRef = useRef<HTMLInputElement | null>(null);
  const fileInputImgRef = useRef<HTMLInputElement | null>(null);
  const fileInputAllRef = useRef<HTMLInputElement | null>(null);

  // Close attachment menu on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setIsMenuOpen(false);
      }
    };
    if (isMenuOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isMenuOpen]);

  const formatFileSize = (bytes: number): string => {
    if (!bytes || bytes <= 0) return "0 B";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const handleFileSelected = async (file: File) => {
    setIsMenuOpen(false);
    if (!file) return;

    const ext = file.name.split(".").pop()?.toLowerCase() || "";
    let fileType: "pdf" | "image" | "document" | "generic" = "generic";
    if (ext === "pdf" || file.type === "application/pdf") {
      fileType = "pdf";
    } else if (file.type.startsWith("image/") || ["png", "jpg", "jpeg", "webp", "gif", "bmp", "svg", "ico"].includes(ext)) {
      fileType = "image";
    } else if (
      file.type.startsWith("text/") ||
      file.type.includes("json") ||
      file.type.includes("javascript") ||
      file.type.includes("typescript") ||
      file.type.includes("xml") ||
      file.type.includes("document") ||
      file.type.includes("sheet") ||
      file.type.includes("presentation") ||
      [
        "docx", "doc", "txt", "csv", "md", "json", "py", "xlsx", "xls", "pptx",
        "ppt", "rtf", "odt", "js", "ts", "jsx", "tsx", "html", "htm", "css",
        "scss", "yaml", "yml", "xml", "sql", "sh", "bat", "ps1", "log", "ini",
        "toml", "c", "cpp", "h", "java", "go", "rs", "php", "rb"
      ].includes(ext)
    ) {
      fileType = "document";
    }

    let previewUrl = "";
    if (fileType === "image") {
      try {
        previewUrl = URL.createObjectURL(file);
      } catch (e) {}
    }

    const item: AttachmentItem = {
      id: `att-${Date.now()}`,
      file,
      filename: file.name,
      fileType,
      size: file.size,
      previewUrl,
      uploadStatus: "uploading",
    };
    setAttachment(item);
    sfx.playClick?.();

    try {
      const res = await uploadFileToBackend(file);
      if (res.success) {
        setAttachment((prev) =>
          prev
            ? {
                ...prev,
                uploadStatus: "success",
                filepath: res.filepath,
                extractedPreview: res.extractedPreview,
              }
            : null
        );
      } else {
        setAttachment((prev) =>
          prev
            ? {
                ...prev,
                uploadStatus: "error",
                error: res.error || "Upload failed",
              }
            : null
        );
      }
    } catch (err: any) {
      console.warn("Upload error:", err);
      setAttachment((prev) =>
        prev
          ? {
              ...prev,
              uploadStatus: "error",
              error: err.message || "Upload failed",
            }
          : null
      );
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isDraggingOver) setIsDraggingOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDraggingOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDraggingOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  };

  // Clipboard paste (Ctrl+V) handler for images, screenshots, and files
  const handlePasteEvent = (e: React.ClipboardEvent | ClipboardEvent) => {
    const clipboardData = (e as any).clipboardData;
    if (!clipboardData) return;

    // 1. Check clipboard files directly
    if (clipboardData.files && clipboardData.files.length > 0) {
      const file = clipboardData.files[0];
      if (file && (file.type.startsWith("image/") || file.name.endsWith(".pdf") || file.size > 0)) {
        e.preventDefault();
        let finalFile = file;
        if (!file.name || file.name === "image.png" || file.name === "blob") {
          const ext = file.type.split("/")[1] || "png";
          finalFile = new File([file], `pasted_image_${Date.now()}.${ext}`, { type: file.type });
        }
        handleFileSelected(finalFile);
        return;
      }
    }

    // 2. Check clipboard items (e.g. screenshots from Win+Shift+S / Snipping Tool / copied images)
    if (clipboardData.items && clipboardData.items.length > 0) {
      for (let i = 0; i < clipboardData.items.length; i++) {
        const item = clipboardData.items[i];
        if (item.kind === "file") {
          const file = item.getAsFile();
          if (file) {
            e.preventDefault();
            let finalFile = file;
            if (!file.name || file.name === "image.png" || file.name === "blob") {
              const ext = file.type.split("/")[1] || "png";
              finalFile = new File([file], `pasted_image_${Date.now()}.${ext}`, { type: file.type });
            }
            handleFileSelected(finalFile);
            return;
          }
        }
      }
    }
  };

  // Global window paste listener for Ctrl+V anywhere in the UI
  useEffect(() => {
    const onWindowPaste = (e: ClipboardEvent) => {
      // Don't intercept if an editable input/textarea other than our voice input is focused and user pastes plain text
      handlePasteEvent(e);
    };
    window.addEventListener("paste", onWindowPaste);
    return () => {
      window.removeEventListener("paste", onWindowPaste);
    };
  }, []);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const speechRecognitionRef = useRef<any>(null);
  const miniCanvasRef = useRef<HTMLCanvasElement | null>(null);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      stopRecordingAndAnalysis();
    };
  }, []);

  // Update live transcript helper
  const updateTranscript = (text: string) => {
    liveTranscriptRef.current = text;
    setLiveTranscript(text);
    onTranscriptChange?.(text);
  };

  // Setup Web Speech Recognition if selected or fallback
  const startWebSpeechRecognition = () => {
    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    try {
      const recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = "en-US";

      recognition.onresult = (event: any) => {
        let interim = "";
        let final = "";
        for (let i = event.resultIndex; i < event.results.length; ++i) {
          if (event.results[i].isFinal) {
            final += event.results[i][0].transcript;
          } else {
            interim += event.results[i][0].transcript;
          }
        }
        const current = final || interim;
        if (current) {
          updateTranscript(current);
        }
      };

      recognition.onerror = (e: any) => {
        console.warn("Web Speech error:", e);
      };

      recognition.start();
      speechRecognitionRef.current = recognition;
    } catch (err) {
      console.warn("Could not start Web Speech:", err);
    }
  };

  // Real Web Audio API microphone stream recording & audio level analyser
  const startRecordingAndAnalysis = async () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("Microphone access is not supported by this browser.");
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      // Set up AudioContext for real-time visualizer waveform
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      const audioCtx = new AudioCtx();
      audioContextRef.current = audioCtx;

      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 64;
      source.connect(analyser);
      analyserRef.current = analyser;

      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      const checkVolume = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteFrequencyData(dataArray);

        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {
          sum += dataArray[i];
        }
        const avg = sum / bufferLength;
        const normalized = Math.min(1.0, avg / 120);

        // Fast zero-overhead broadcast to Canvas Visualizer via AudioBus (prevents 60fps React state re-render thrashing)
        audioBus.emit(normalized, dataArray);

        // Render mini canvas waveform directly with zero React re-render overhead
        if (miniCanvasRef.current) {
          const mCanvas = miniCanvasRef.current;
          const mCtx = mCanvas.getContext("2d");
          if (mCtx) {
            mCtx.clearRect(0, 0, mCanvas.width, mCanvas.height);
            const numBars = 6;
            const barWidth = 2.5;
            const gap = 2;
            const totalW = numBars * (barWidth + gap);
            const startX = (mCanvas.width - totalW) / 2;

            for (let b = 0; b < numBars; b++) {
              const val = (dataArray[b * 2] || 0) / 255;
              const barH = Math.max(3, val * mCanvas.height * 0.85);
              const x = startX + b * (barWidth + gap);
              const y = (mCanvas.height - barH) / 2;

              mCtx.fillStyle = b % 2 === 0 ? theme.primary : theme.accent;
              mCtx.beginPath();
              mCtx.roundRect(x, y, barWidth, barH, 1.5);
              mCtx.fill();
            }
          }
        }

        animFrameRef.current = requestAnimationFrame(checkVolume);
      };

      animFrameRef.current = requestAnimationFrame(checkVolume);

      if (backendConfig?.transcriptionEngine === "web-speech") {
        startWebSpeechRecognition();
      }

      // Capture audio chunks for STT
      audioChunksRef.current = [];
      const options = { mimeType: "audio/webm" };
      let recorder: MediaRecorder;
      try {
        recorder = new MediaRecorder(stream, options);
      } catch (e) {
        recorder = new MediaRecorder(stream);
      }

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          audioChunksRef.current.push(e.data);
        }
      };

      recorder.onstop = async () => {
        const spoken = liveTranscriptRef.current.trim();
        if (spoken) {
          onProcessCommand(spoken);
          updateTranscript("");
          return;
        }

        const audioBlob = new Blob(audioChunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });

        if (audioBlob.size > 0) {
          setIsTranscribing(true);
          try {
            const text = await transcribeAudio(
              audioBlob,
              backendConfig?.transcriptionUrl,
              backendConfig?.apiKey
            );
            if (text && text.trim()) {
              onProcessCommand(text.trim());
            }
          } catch (err: any) {
            console.warn("Transcription error:", err);
          } finally {
            setIsTranscribing(false);
            updateTranscript("");
          }
        }
      };

      recorder.start(250);
      mediaRecorderRef.current = recorder;
    } catch (err: any) {
      console.warn("Microphone access error:", err);
      onSetListening(false);
    }
  };

  const stopRecordingAndAnalysis = () => {
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    audioBus.emit(0);

    if (speechRecognitionRef.current) {
      try {
        speechRecognitionRef.current.stop();
      } catch (e) {}
      speechRecognitionRef.current = null;
    }

    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      try {
        mediaRecorderRef.current.stop();
      } catch (e) {}
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }

    if (audioContextRef.current && audioContextRef.current.state !== "closed") {
      try {
        audioContextRef.current.close();
      } catch (e) {}
      audioContextRef.current = null;
    }
  };

  const handleToggleListening = () => {
    if (disabled || isTranscribing) return;

    if (isListening) {
      sfx.playMicOff();
      stopRecordingAndAnalysis();
      onSetListening(false);
    } else {
      sfx.playMicOn();
      updateTranscript("");
      onSetListening(true);
      startRecordingAndAnalysis();
    }
  };

  const handleSubmitText = (e: React.FormEvent) => {
    e.preventDefault();
    if (disabled || isListening || isTranscribing) return;
    const command = inputText.trim();
    if (!command && !attachment) return;

    sfx.playSubmit();
    const promptToSend =
      command ||
      (attachment
        ? `Analyze ${attachment.filename}`
        : "");
    const currentAttachment = attachment || undefined;

    setInputText("");
    setAttachment(null);
    onProcessCommand(promptToSend, currentAttachment);
  };

  return (
    <div className="w-full max-w-2xl mx-auto flex flex-col items-center px-3 pb-3 sm:pb-4">
      {/* Hidden file inputs */}
      <input
        ref={fileInputDocRef}
        type="file"
        accept=".pdf,.docx,.doc,.txt,.csv,.md,.json,.py,.js,.ts,.tsx,.html,.css,.yaml,.yml,.xml,.sql,.xlsx,.xls,.pptx,.rtf,.odt"
        className="hidden"
        onChange={(e) => {
          if (e.target.files && e.target.files[0]) {
            handleFileSelected(e.target.files[0]);
            e.target.value = "";
          }
        }}
      />
      <input
        ref={fileInputImgRef}
        type="file"
        accept="image/*,.png,.jpg,.jpeg,.webp,.gif,.bmp,.svg,.ico"
        className="hidden"
        onChange={(e) => {
          if (e.target.files && e.target.files[0]) {
            handleFileSelected(e.target.files[0]);
            e.target.value = "";
          }
        }}
      />
      <input
        ref={fileInputAllRef}
        type="file"
        accept="*/*"
        className="hidden"
        onChange={(e) => {
          if (e.target.files && e.target.files[0]) {
            handleFileSelected(e.target.files[0]);
            e.target.value = "";
          }
        }}
      />

      {/* Attachment Preview Chip */}
      <AnimatePresence>
        {attachment && (
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.95 }}
            transition={{ duration: 0.2 }}
            className={`mb-2 w-full flex items-center justify-between px-3 py-2 rounded-2xl backdrop-blur-2xl border shadow-lg transition-all ${
              isDark
                ? "bg-slate-900/85 border-white/15 text-slate-100"
                : "bg-white/95 border-black/10 text-slate-800"
            }`}
          >
            <div className="flex items-center space-x-2.5 min-w-0 flex-1">
              {attachment.fileType === "image" && attachment.previewUrl ? (
                <div className="relative w-9 h-9 rounded-xl overflow-hidden border border-white/20 flex-shrink-0 bg-black/20">
                  <img
                    src={attachment.previewUrl}
                    alt={attachment.filename}
                    className="w-full h-full object-cover"
                  />
                </div>
              ) : attachment.fileType === "pdf" ? (
                <div className="w-9 h-9 rounded-xl bg-rose-500/20 border border-rose-500/30 flex items-center justify-center flex-shrink-0 text-rose-400">
                  <FileText className="w-5 h-5" />
                </div>
              ) : (
                <div className="w-9 h-9 rounded-xl bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center flex-shrink-0 text-indigo-400">
                  <FileCode className="w-5 h-5" />
                </div>
              )}

              <div className="min-w-0 flex-1 pr-2">
                <p className="text-xs sm:text-sm font-medium truncate">
                  {attachment.filename}
                </p>
                <div className="flex items-center space-x-2 text-[11px] opacity-75">
                  <span>{formatFileSize(attachment.size)}</span>
                  <span>•</span>
                  {attachment.uploadStatus === "uploading" ? (
                    <span className="flex items-center space-x-1 text-amber-400 font-medium">
                      <Loader2 className="w-3 h-3 animate-spin" />
                      <span>Indexing document...</span>
                    </span>
                  ) : attachment.uploadStatus === "success" ? (
                    <span className="flex items-center space-x-1 text-emerald-400 font-medium">
                      <CheckCircle2 className="w-3 h-3" />
                      <span>Ready & Indexed</span>
                    </span>
                  ) : attachment.uploadStatus === "error" ? (
                    <span className="flex items-center space-x-1 text-rose-400 font-medium">
                      <AlertCircle className="w-3 h-3" />
                      <span>{attachment.error || "Upload failed"}</span>
                    </span>
                  ) : (
                    <span>Ready</span>
                  )}
                </div>
              </div>
            </div>

            <button
              type="button"
              onClick={() => {
                sfx.playClick?.();
                setAttachment(null);
              }}
              className={`p-1.5 rounded-full transition-all hover:scale-110 active:scale-95 ${
                isDark
                  ? "hover:bg-white/15 text-slate-400 hover:text-white"
                  : "hover:bg-black/10 text-slate-500 hover:text-black"
              }`}
              title="Remove attachment"
            >
              <X className="w-4 h-4" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Search / Voice input fluid capsule */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`voice-input-shell w-full flex items-center p-1.5 sm:p-2 rounded-2xl sm:rounded-full backdrop-blur-2xl border transition-all duration-300 ${
          isDraggingOver
            ? "shadow-2xl scale-[1.015] border-dashed"
            : isListening
            ? "shadow-2xl scale-[1.01]"
            : isFocused
            ? "shadow-xl scale-[1.005]"
            : "shadow-lg hover:shadow-xl"
        } ${
          isDark
            ? "text-slate-100"
            : "text-slate-900"
        }`}
        style={{
          backgroundColor: isDraggingOver
            ? isDark
              ? `${theme.primary}30`
              : `${theme.primary}15`
            : isDark
            ? colorTheme === "noir"
              ? "rgba(18, 18, 22, 0.75)"
              : `${theme.primary}12`
            : "rgba(255, 255, 255, 0.95)",
          boxShadow: isDraggingOver
            ? `0 0 25px ${theme.glow}, 0 6px 20px rgba(0,0,0,0.3)`
            : isListening
            ? `0 0 18px ${theme.glow}, 0 6px 20px rgba(0,0,0,0.3)`
            : isFocused
            ? isDark
              ? `0 4px 18px rgba(0,0,0,0.35), 0 0 8px ${theme.glow}`
              : `0 4px 14px rgba(0,0,0,0.05), 0 0 6px ${theme.glow}30`
            : isDark
            ? colorTheme === "noir"
              ? `0 4px 16px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.06)`
              : `0 4px 16px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.08)`
            : `0 3px 12px rgba(0,0,0,0.03), inset 0 1px 0 rgba(255,255,255,0.9)`,
          borderColor: isDraggingOver || isListening || isFocused
            ? (theme.accent || theme.primary)
            : isDark
            ? colorTheme === "noir"
              ? "rgba(255, 255, 255, 0.12)"
              : `${theme.primary}35`
            : "rgba(0, 0, 0, 0.10)",
          "--voice-glow": theme.glow,
          "--voice-accent": theme.accent || theme.primary,
        } as React.CSSProperties}
      >
        {/* Form and Text / Live transcript container */}
        <form
          onSubmit={handleSubmitText}
          autoComplete="off"
          className="flex-1 flex items-center min-w-0"
        >
          {/* Plus (+) Attachment Button & Popover */}
          <div className="relative flex items-center pl-1 sm:pl-1.5 flex-shrink-0" ref={menuRef}>
            <button
              id="attach-file-button"
              type="button"
              onClick={() => {
                sfx.playClick?.();
                setIsMenuOpen(!isMenuOpen);
              }}
              disabled={disabled || isListening || isTranscribing}
              className={`relative w-8 h-8 sm:w-9 sm:h-9 rounded-full transition-all duration-200 flex items-center justify-center active:scale-95 hover:scale-105 border ${
                isMenuOpen
                  ? "shadow-md border-transparent text-white"
                  : isDark
                  ? "hover:bg-white/10 text-slate-300 border-white/10"
                  : "hover:bg-black/5 text-slate-700 border-black/10"
              }`}
              style={
                isMenuOpen
                  ? {
                      background: theme.gradient,
                      boxShadow: `0 0 14px ${theme.glow}`,
                      transform: "rotate(45deg)",
                    }
                  : undefined
              }
              title="Attach files, PDFs, or images"
            >
              <Plus className="w-4 h-4 sm:w-4.5 sm:h-4.5 transition-transform duration-200" />
            </button>

            {/* Popover Flyout Menu */}
            <AnimatePresence>
              {isMenuOpen && (
                <motion.div
                  initial={{ opacity: 0, y: 10, scale: 0.95 }}
                  animate={{ opacity: 1, y: -8, scale: 1 }}
                  exit={{ opacity: 0, y: 8, scale: 0.95 }}
                  transition={{ duration: 0.16, ease: "easeOut" }}
                  className={`absolute bottom-full left-0 mb-2 w-64 rounded-2xl p-1.5 shadow-2xl backdrop-blur-2xl border z-50 ${
                    isDark
                      ? "bg-slate-900/95 border-white/15 text-slate-100"
                      : "bg-white/95 border-black/10 text-slate-900"
                  }`}
                  style={{
                    boxShadow: isDark
                      ? "0 12px 36px rgba(0,0,0,0.6), 0 0 1px rgba(255,255,255,0.2)"
                      : "0 12px 36px rgba(0,0,0,0.15), 0 0 1px rgba(0,0,0,0.1)",
                  }}
                >
                  <div className="px-2.5 py-1.5 border-b border-white/10 mb-1">
                    <p className="text-[11px] font-semibold tracking-wider uppercase opacity-60">
                      Add to message
                    </p>
                  </div>

                  {/* 1. Document / PDF */}
                  <button
                    type="button"
                    onClick={() => {
                      fileInputDocRef.current?.click();
                      setIsMenuOpen(false);
                    }}
                    className={`w-full flex items-center space-x-3 px-2.5 py-2 rounded-xl transition-all text-left ${
                      isDark ? "hover:bg-white/10" : "hover:bg-black/5"
                    }`}
                  >
                    <div className="w-8 h-8 rounded-lg bg-rose-500/20 text-rose-400 flex items-center justify-center flex-shrink-0 border border-rose-500/30">
                      <FileText className="w-4 h-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs sm:text-sm font-medium">Document / PDF</p>
                      <p className="text-[10px] sm:text-[11px] opacity-60 truncate">PDF, Word, TXT, CSV, Code</p>
                    </div>
                  </button>

                  {/* 2. Image */}
                  <button
                    type="button"
                    onClick={() => {
                      fileInputImgRef.current?.click();
                      setIsMenuOpen(false);
                    }}
                    className={`w-full flex items-center space-x-3 px-2.5 py-2 rounded-xl transition-all text-left ${
                      isDark ? "hover:bg-white/10" : "hover:bg-black/5"
                    }`}
                  >
                    <div className="w-8 h-8 rounded-lg bg-sky-500/20 text-sky-400 flex items-center justify-center flex-shrink-0 border border-sky-500/30">
                      <ImageIcon className="w-4 h-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs sm:text-sm font-medium">Image or Photo</p>
                      <p className="text-[10px] sm:text-[11px] opacity-60 truncate">PNG, JPG, WEBP, Diagrams</p>
                    </div>
                  </button>

                  {/* 3. All Files */}
                  <button
                    type="button"
                    onClick={() => {
                      fileInputAllRef.current?.click();
                      setIsMenuOpen(false);
                    }}
                    className={`w-full flex items-center space-x-3 px-2.5 py-2 rounded-xl transition-all text-left ${
                      isDark ? "hover:bg-white/10" : "hover:bg-black/5"
                    }`}
                  >
                    <div className="w-8 h-8 rounded-lg bg-violet-500/20 text-violet-400 flex items-center justify-center flex-shrink-0 border border-violet-500/30">
                      <Folder className="w-4 h-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs sm:text-sm font-medium">Browse All Files</p>
                      <p className="text-[10px] sm:text-[11px] opacity-60 truncate">Any local file</p>
                    </div>
                  </button>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          <div className="relative flex-1 min-w-0">
            {isListening ? (
              <div className="flex items-center justify-between py-2 px-3 sm:px-4 min-h-[44px]">
                <div className="flex-1 min-w-0 pr-3">
                  {liveTranscript ? (
                    <p className="min-w-0 break-words text-sm font-medium leading-relaxed text-slate-100">
                      {liveTranscript}
                    </p>
                  ) : (
                    <div className="flex items-center space-x-2.5">
                      <Radio className="w-4 h-4 animate-pulse flex-shrink-0" style={{ color: theme.accent }} />
                      <span
                        className="text-xs sm:text-sm font-medium tracking-wide animate-pulse truncate"
                        style={{ color: theme.accent }}
                      >
                        Listening to your voice...
                      </span>
                    </div>
                  )}
                </div>
                {/* Zero-overhead 60fps mini waveform canvas */}
                <canvas
                  ref={miniCanvasRef}
                  width={40}
                  height={22}
                  className="w-10 h-5 flex-shrink-0"
                />
              </div>
            ) : (
              <div className="relative w-full flex items-center">
                <input
                  id="voice-text-input"
                  name="amigo_search_query_prompt"
                  type="text"
                  autoComplete="off"
                  autoCorrect="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  data-lpignore="true"
                  data-form-type="other"
                  value={isTranscribing ? "Transcribing speech..." : inputText}
                  onChange={(e) => setInputText(e.target.value)}
                  onFocus={() => setIsFocused(true)}
                  onBlur={() => setIsFocused(false)}
                  onPaste={handlePasteEvent}
                  disabled={disabled || isListening || isTranscribing}
                  placeholder={attachment ? `Ask about ${attachment.filename}...` : "Ask anything or use voice commands..."}
                  className={`w-full py-2.5 sm:py-3 pl-2.5 sm:pl-3.5 pr-3 text-xs sm:text-sm font-normal bg-transparent focus:outline-none transition-all text-input-glowing-cursor ${
                    isDark
                      ? "text-slate-100 placeholder:text-slate-400"
                      : "text-slate-900 placeholder:text-slate-400"
                  }`}
                  style={{
                    caretColor: theme.accent || theme.primary,
                    "--theme-caret-color": theme.accent || theme.primary,
                    "--theme-caret-glow-color": theme.accent || theme.primary,
                  } as React.CSSProperties}
                />
              </div>
            )}
          </div>

          {/* Action Group: Microphone + Submit Button */}
          <div className="flex items-center space-x-1.5 sm:space-x-2 pr-1 flex-shrink-0">
            {/* Fluid Microphone Button with Concentric Rings */}
            <div className="relative flex items-center justify-center">
              {isListening && (
                <>
                  <motion.span
                    initial={{ scale: 0.85, opacity: 0.8 }}
                    animate={{ scale: 1.6, opacity: 0 }}
                    transition={{ repeat: Infinity, duration: 1.6, ease: "easeOut" }}
                    className="absolute inset-0 rounded-full bg-rose-500/50 pointer-events-none"
                  />
                  <motion.span
                    initial={{ scale: 0.85, opacity: 0.9 }}
                    animate={{ scale: 1.35, opacity: 0 }}
                    transition={{ repeat: Infinity, duration: 1.6, ease: "easeOut", delay: 0.4 }}
                    className="absolute inset-0 rounded-full bg-red-400/60 pointer-events-none"
                  />
                </>
              )}

              <button
                id="voice-toggle-button"
                type="button"
                onClick={handleToggleListening}
                disabled={disabled || isTranscribing}
                className={`relative w-9 h-9 sm:w-10 sm:h-10 rounded-full transition-all duration-300 flex items-center justify-center active:scale-95 hover:-translate-y-0.5 z-10 overflow-hidden border ${
                  isListening
                    ? "text-white shadow-lg border-transparent"
                    : isDark
                    ? "hover:brightness-125"
                    : "hover:brightness-95"
                }`}
                style={
                  isListening
                    ? {
                        background: "linear-gradient(135deg, #f43f5e 0%, #e11d48 50%, #be123c 100%)",
                        boxShadow: "0 0 25px rgba(244, 63, 94, 0.75), inset 0 1px 1px rgba(255, 255, 255, 0.4)",
                      }
                    : {
                        backgroundColor: isDark ? `${theme.primary}25` : `${theme.primary}15`,
                        borderColor: `${theme.primary}50`,
                        color: theme.accent || theme.primary,
                      }
                }
                title={isListening ? "Stop listening" : "Click to speak voice command"}
              >
                {isListening ? (
                  <Square className="w-3.5 h-3.5 fill-white text-white animate-pulse relative z-10" />
                ) : isTranscribing ? (
                  <Loader2 className="w-4 h-4 animate-spin relative z-10 text-indigo-400" />
                ) : (
                  <Mic className="w-4 h-4 relative z-10 transition-transform group-hover:scale-105" />
                )}
              </button>
            </div>

            {/* Submit button */}
            <button
              id="send-command-button"
              type="submit"
              disabled={disabled || (!inputText.trim() && !attachment) || isListening || isTranscribing}
              className={`w-9 h-9 sm:w-10 sm:h-10 rounded-full transition-all duration-200 active:scale-95 hover:-translate-y-0.5 flex items-center justify-center flex-shrink-0 ${
                inputText.trim() || attachment
                  ? "text-white shadow-md cursor-pointer hover:brightness-110"
                  : isDark
                  ? "cursor-not-allowed border"
                  : "cursor-not-allowed border"
              }`}
              style={
                inputText.trim() || attachment
                  ? {
                      background: theme.gradient,
                      boxShadow: `0 4px 14px ${theme.glow}, inset 0 1px 1px rgba(255,255,255,0.35)`,
                    }
                  : {
                      backgroundColor: isDark ? `${theme.primary}12` : `${theme.primary}0a`,
                      borderColor: `${theme.primary}25`,
                      color: `${theme.primary}60`,
                    }
              }
              title={attachment ? "Send message with attachment (Enter)" : "Send message (Enter)"}
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
