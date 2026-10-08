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
  File as FileIcon,
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
import { HaloSearchInput } from "@/components/ui/halo-search";

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
  isLoading?: boolean;
  loadingText?: string;
}

export const VoiceControls: React.FC<VoiceControlsProps> = ({
  onProcessCommand,
  isListening,
  onSetListening,
  onAudioLevelChange,
  onTranscriptChange,
  isDark,
  colorTheme = "beams",
  disabled = false,
  backendConfig,
  isLoading = false,
  loadingText = "Searching...",
}) => {
  const [inputText, setInputText] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState("");
  const liveTranscriptRef = useRef("");
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.beams || COLOR_THEMES.violet;

  // File attachment & popover state
  const [attachment, setAttachment] = useState<AttachmentItem | null>(null);

  const isAttachmentPending = Boolean(
    attachment && (attachment.uploadStatus === "uploading" || attachment.uploadStatus === "error")
  );

  // Keep latest callbacks & configs in refs to avoid stale closures in recorder callbacks
  const onProcessCommandRef = useRef(onProcessCommand);
  useEffect(() => {
    onProcessCommandRef.current = onProcessCommand;
  }, [onProcessCommand]);

  const backendConfigRef = useRef(backendConfig);
  useEffect(() => {
    backendConfigRef.current = backendConfig;
  }, [backendConfig]);

  const discardOnStopRef = useRef(false);
  const recordingSessionIdRef = useRef(0);
  const attachmentRef = useRef<AttachmentItem | null>(null);

  useEffect(() => {
    attachmentRef.current = attachment;
  }, [attachment]);

  useEffect(() => {
    if (!isLoading) {
      setIsSubmitting(false);
    }
  }, [isLoading]);

  const clearAttachment = () => {
    if (attachmentRef.current?.previewUrl) {
      try {
        URL.revokeObjectURL(attachmentRef.current.previewUrl);
      } catch (_) {}
    }
    setAttachment(null);
  };

  const [isMenuOpen, setIsMenuOpen] = useState<boolean>(false);
  const [isDraggingOver, setIsDraggingOver] = useState<boolean>(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const openFileDialog = (accept: string) => {
    if (fileInputRef.current) {
      fileInputRef.current.accept = accept;
      fileInputRef.current.click();
    }
    setIsMenuOpen(false);
  };

  // Close attachment menu on outside click or Escape key
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setIsMenuOpen(false);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsMenuOpen(false);
      }
    };
    if (isMenuOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      document.addEventListener("keydown", handleKeyDown);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
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

    // Revoke previous object URL if any
    if (attachmentRef.current?.previewUrl) {
      try {
        URL.revokeObjectURL(attachmentRef.current.previewUrl);
      } catch (_) {}
    }

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
    const currentAttachmentId = item.id;
    setAttachment(item);
    sfx.playClick?.();

    try {
      const res = await uploadFileToBackend(file);
      if (res.success) {
        setAttachment((prev) =>
          prev && prev.id === currentAttachmentId
            ? {
                ...prev,
                uploadStatus: "success",
                filepath: res.filepath,
                extractedPreview: res.extractedPreview,
              }
            : prev
        );
      } else {
        setAttachment((prev) =>
          prev && prev.id === currentAttachmentId
            ? {
                ...prev,
                uploadStatus: "error",
                error: res.error || "Upload failed",
              }
            : prev
        );
      }
    } catch (err: any) {
      console.warn("Upload error:", err);
      setAttachment((prev) =>
        prev && prev.id === currentAttachmentId
          ? {
              ...prev,
              uploadStatus: "error",
              error: err.message || "Upload failed",
            }
          : prev
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
  const handlePasteEvent = (e: ClipboardEvent) => {
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

  // Global window paste listener for Ctrl+V anywhere in the UI (single listener, avoids duplicate paste execution)
  useEffect(() => {
    const onWindowPaste = (e: ClipboardEvent) => {
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
  const webSpeechActiveRef = useRef<boolean>(false);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      discardOnStopRef.current = true;
      recordingSessionIdRef.current++;
      stopRecordingAndAnalysis();
      if (attachmentRef.current?.previewUrl) {
        try {
          URL.revokeObjectURL(attachmentRef.current.previewUrl);
        } catch (_) {}
      }
    };
  }, []);

  // Synchronize recording state whenever isListening prop changes (from Click, Orb, Spacebar, or SSE)
  const isListeningRef = useRef(false);
  useEffect(() => {
    if (isListening && !isListeningRef.current) {
      isListeningRef.current = true;
      updateTranscript("");
      startRecordingAndAnalysis();
    } else if (!isListening && isListeningRef.current) {
      isListeningRef.current = false;
      stopRecordingAndAnalysis();
    }
  }, [isListening]);

  // Update live transcript helper
  const updateTranscript = (text: string) => {
    liveTranscriptRef.current = text;
    setLiveTranscript(text);
    onTranscriptChange?.(text);
  };

  // Setup Web Speech Recognition only if explicitly configured
  const startWebSpeechRecognition = () => {
    if (backendConfigRef.current?.transcriptionEngine !== "web-speech") return;
    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    try {
      const recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = "en-US";
      webSpeechActiveRef.current = false;

      recognition.onresult = (event: any) => {
        webSpeechActiveRef.current = true;
        let fullTranscript = "";
        for (let i = 0; i < event.results.length; ++i) {
          fullTranscript += event.results[i][0].transcript;
        }
        const current = fullTranscript.trim();
        if (current) {
          updateTranscript(current);
        }
      };

      recognition.onerror = (e: any) => {
        console.warn("Web Speech error:", e);
        webSpeechActiveRef.current = false;
        // If offline network error or speech recognition fails, fall back to offline Sherpa-ONNX STT
        if (e.error === "network" || e.error === "not-allowed" || e.error === "service-not-allowed") {
          try {
            recognition.stop();
          } catch (_) {}
          speechRecognitionRef.current = null;
        }
      };

      recognition.onend = () => {
        webSpeechActiveRef.current = false;
      };

      recognition.start();
      speechRecognitionRef.current = recognition;
    } catch (err) {
      console.warn("Could not start Web Speech:", err);
      webSpeechActiveRef.current = false;
      speechRecognitionRef.current = null;
    }
  };

  // Real Web Audio API microphone stream recording & audio level analyser
  const startRecordingAndAnalysis = async () => {
    const sessionId = ++recordingSessionIdRef.current;
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

      // Cancellation check: if user toggled off or unmounted before getUserMedia resolved, abort immediately
      if (sessionId !== recordingSessionIdRef.current || !isListeningRef.current || discardOnStopRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      streamRef.current = stream;

      // Set up AudioContext for real-time microphone level analysis
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      const audioCtx = new AudioCtx();
      if (audioCtx.state === "suspended") {
        try {
          await audioCtx.resume();
        } catch (_) {}
      }
      audioContextRef.current = audioCtx;

      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 64;
      source.connect(analyser);
      analyserRef.current = analyser;

      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      const recordingStartTime = performance.now();
      let lastSpeechTime = performance.now();
      let hasGenuineSpeech = false;
      let consecutiveSpeechFrames = 0;
      let totalVoicedFrames = 0;
      let noiseFloorSum = 0;
      let noiseFloorSamples = 0;
      let noiseFloor = 0.12;

      const checkVolume = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteFrequencyData(dataArray);

        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {
          sum += dataArray[i];
        }
        const avg = sum / bufferLength;
        const normalized = Math.min(1.0, avg / 120);

        onAudioLevelChange?.(normalized);
        audioBus.emit(normalized, dataArray);

        const now = performance.now();

        // Isolate vocal frequencies (bins 0-4 covering ~100Hz - 3750Hz fundamental & formant frequencies)
        // This rejects high-frequency fan hiss, keyboard clicks, and white noise that average across all bins
        let vocalSum = 0;
        const vocalBins = Math.min(5, bufferLength);
        for (let i = 0; i < vocalBins; i++) {
          vocalSum += dataArray[i];
        }
        const vocalNormalized = Math.min(1.0, (vocalSum / vocalBins) / 110);

        // Dynamically estimate ambient room noise floor during the initial silence period (< 800ms)
        if (!hasGenuineSpeech && now - recordingStartTime < 800) {
          noiseFloorSum += vocalNormalized;
          noiseFloorSamples++;
          noiseFloor = noiseFloorSum / noiseFloorSamples;
        } else if (!hasGenuineSpeech) {
          // Continuously track shifting background room noise (fans, air conditioning, room reflections)
          noiseFloor = noiseFloor * 0.985 + vocalNormalized * 0.015;
        }

        // Voice must distinctly exceed ambient noise floor (+ 0.20 margin, min 0.32)
        const speechThreshold = Math.max(0.32, noiseFloor + 0.20);
        const hasWebSpeechText = webSpeechActiveRef.current && liveTranscriptRef.current.trim().length > 0;

        if (vocalNormalized > speechThreshold || hasWebSpeechText) {
          consecutiveSpeechFrames++;
          totalVoicedFrames++;
          // Require at least ~450ms (28 consecutive frames) of sustained voice energy
          if (consecutiveSpeechFrames >= 28 || hasWebSpeechText) {
            hasGenuineSpeech = true;
            lastSpeechTime = now;
          }
        } else {
          consecutiveSpeechFrames = Math.max(0, consecutiveSpeechFrames - 2);
        }

        // Only auto-stop on silence AFTER the user has actually spoken genuine speech!
        // Never auto-stop while the user is simply pausing or thinking before speaking.
        if (hasGenuineSpeech && now - lastSpeechTime > 3500) {
          onSetListening(false);
          return;
        }

        // Maximum recording safety duration: 90 seconds
        if (now - recordingStartTime > 90000) {
          onSetListening(false);
          return;
        }

        animFrameRef.current = requestAnimationFrame(checkVolume);
      };

      animFrameRef.current = requestAnimationFrame(checkVolume);

      // 1. Real-time visual speech streaming: ONLY start browser Web Speech if explicitly configured (protects local privacy)
      const isWebSpeechConfigured = backendConfigRef.current?.transcriptionEngine === "web-speech";
      const SpeechRec = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (SpeechRec && isWebSpeechConfigured) {
        startWebSpeechRecognition();
      }

      // 2. Capture audio chunks for Sherpa-ONNX offline neural STT
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
        // If unmounted or cancelled, never send partial command
        if (discardOnStopRef.current) {
          return;
        }

        const spoken = liveTranscriptRef.current.trim();
        const audioBlob = new Blob(audioChunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });

        const cfg = backendConfigRef.current;
        const processCommand = onProcessCommandRef.current;

        // If user never actually sustained genuine speech (requires at least ~400ms / 25 frames of total vocalization)
        // and there is no genuine transcript, discard quietly without sending phantom audio to backend!
        if ((!hasGenuineSpeech || totalVoicedFrames < 25) && !spoken) {
          updateTranscript("");
          return;
        }

        if (audioBlob.size > 0 && hasGenuineSpeech) {
          // If user configured web-speech and we already have real-time spoken text, process directly
          if (spoken && cfg?.transcriptionEngine === "web-speech") {
            processCommand(spoken);
            updateTranscript("");
            return;
          }

          setIsTranscribing(true);
          try {
            const text = await transcribeAudio(
              audioBlob,
              cfg?.transcriptionUrl,
              cfg?.apiKey
            );
            const finalText = (text && text.trim()) || spoken;
            const cleanText = finalText.replace(/^[.\s,?!-]+|[.\s,?!-]+$/g, "").trim();
            if (cleanText && cleanText.length >= 2 && !discardOnStopRef.current) {
              processCommand(cleanText);
            }
          } catch (err: any) {
            console.warn("Transcription error:", err);
            const cleanSpoken = spoken.replace(/^[.\s,?!-]+|[.\s,?!-]+$/g, "").trim();
            if (cleanSpoken && cleanSpoken.length >= 2 && !discardOnStopRef.current) {
              processCommand(cleanSpoken);
            }
          } finally {
            setIsTranscribing(false);
            updateTranscript("");
          }
        } else if (spoken && hasGenuineSpeech && !discardOnStopRef.current) {
          const cleanSpoken = spoken.replace(/^[.\s,?!-]+|[.\s,?!-]+$/g, "").trim();
          if (cleanSpoken && cleanSpoken.length >= 2) {
            processCommand(cleanSpoken);
          }
          updateTranscript("");
        } else {
          updateTranscript("");
        }
      };

      recorder.start(250);
      mediaRecorderRef.current = recorder;
    } catch (err: any) {
      console.warn("Microphone access error:", err);
      if (audioContextRef.current && audioContextRef.current.state !== "closed") {
        try {
          audioContextRef.current.close();
        } catch (_) {}
        audioContextRef.current = null;
      }
      onSetListening(false);
    }
  };

  const stopRecordingAndAnalysis = () => {
    recordingSessionIdRef.current++;
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
    webSpeechActiveRef.current = false;

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
      onSetListening(false);
    } else {
      sfx.playMicOn();
      onSetListening(true);
    }
  };

  const handleSubmitText = (e: React.FormEvent) => {
    e.preventDefault();
    if (disabled || isListening || isTranscribing || isLoading || isSubmitting || isAttachmentPending) return;
    const command = inputText.trim();
    if (!command && !attachment) return;

    sfx.playSubmit();
    const promptToSend =
      command ||
      (attachment
        ? `Analyze ${attachment.filename}`
        : "");
    const currentAttachment = attachment || undefined;

    setIsSubmitting(true);
    setInputText("");
    clearAttachment();
    try {
      onProcessCommandRef.current(promptToSend, currentAttachment);
    } catch (err) {
      // Restore input text so user does not lose typed query on failure
      setInputText(command);
      setIsSubmitting(false);
    }
  };

  return (
    <div className="w-full max-w-2xl mx-auto flex flex-col items-center px-3 pb-[max(0.5rem,calc(env(safe-area-inset-bottom)+0.25rem))]">
      {/* Single unified hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        tabIndex={-1}
        aria-hidden="true"
        style={{ display: "none" }}
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
                clearAttachment();
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

      {/* Cult UI Halo Search Bar - replacing older search input capsule */}
      {(() => {
        const isSearching = Boolean(isLoading || isSubmitting || isTranscribing);
        return (
          <form
            onSubmit={handleSubmitText}
            autoComplete="off"
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className="w-full flex justify-center"
          >
            <HaloSearchInput
              id="voice-text-input"
              name="amigo_search_query_prompt"
              value={isSearching ? "" : inputText}
              onChange={(e) => setInputText(e.target.value)}
              onClear={() => setInputText("")}
              onCancel={() => {
                setIsSubmitting(false);
                setIsTranscribing(false);
              }}
              placeholder={attachment ? `Ask about ${attachment.filename}...` : "Ask anything or search"}
              isLoading={isSearching}
              loadingText={isTranscribing ? "Listening to your voice..." : (loadingText || "Searching...")}
              isListening={isListening}
              listeningText={liveTranscript}
              disabled={disabled || isListening}
              className="w-full max-w-3xl"
              onFocus={() => {
                setIsFocused(true);
                setTimeout(() => window.scrollTo(0, 0), 50);
              }}
              onBlur={() => setIsFocused(false)}
          leftSlot={
            /* GPU-composited Spring-animated Attachment Menu */
            <div className="relative z-30 flex items-center flex-shrink-0" ref={menuRef}>
              <AnimatePresence>
                {isMenuOpen && (
                  <>
                    {/* Top Circle: Image / Photo */}
                    <motion.div
                      key="attach-img"
                      initial={{ opacity: 0, scale: 0.3, x: 0, y: 0 }}
                      animate={{ opacity: 1, scale: 1, x: 0, y: -54 }}
                      exit={{ opacity: 0, scale: 0.3, x: 0, y: 0 }}
                      transition={{ type: "spring", stiffness: 500, damping: 28, mass: 0.8 }}
                      className="absolute top-0 left-0 w-9 h-9 pointer-events-auto"
                    >
                      <button
                        type="button"
                        onClick={() => {
                          sfx.playClick?.();
                          openFileDialog("image/*,.png,.jpg,.jpeg,.webp,.gif,.bmp,.svg,.ico");
                        }}
                        className={`w-9 h-9 rounded-full flex items-center justify-center transition-transform duration-150 hover:scale-110 active:scale-95 focus:outline-none cursor-pointer border shadow-lg ${
                          isDark
                            ? "border-sky-500/40 bg-slate-900/95 text-sky-400 hover:bg-sky-500/20 hover:text-sky-300 hover:border-sky-400 hover:shadow-[0_0_14px_rgba(56,189,248,0.4)]"
                            : "border-sky-500/30 bg-white text-sky-600 hover:bg-sky-500/15 hover:text-sky-700 shadow-sky-500/10"
                        }`}
                        title="Attach Image or Photo (PNG, JPG, WEBP, SVG)"
                        aria-label="Attach Image"
                      >
                        <ImageIcon className="w-4.5 h-4.5" strokeWidth={1.8} />
                      </button>
                    </motion.div>

                    {/* Left Circle: Document / PDF */}
                    <motion.div
                      key="attach-doc"
                      initial={{ opacity: 0, scale: 0.3, x: 0, y: 0 }}
                      animate={{ opacity: 1, scale: 1, x: -34, y: -28 }}
                      exit={{ opacity: 0, scale: 0.3, x: 0, y: 0 }}
                      transition={{ type: "spring", stiffness: 500, damping: 28, mass: 0.8, delay: 0.02 }}
                      className="absolute top-0 left-0 w-9 h-9 pointer-events-auto"
                    >
                      <button
                        type="button"
                        onClick={() => {
                          sfx.playClick?.();
                          openFileDialog(".pdf,.docx,.doc,.txt,.csv,.md,.json,.py,.js,.ts,.tsx,.html,.css,.yaml,.yml,.xml,.sql,.xlsx,.xls,.pptx,.rtf,.odt");
                        }}
                        className={`w-9 h-9 rounded-full flex items-center justify-center transition-transform duration-150 hover:scale-110 active:scale-95 focus:outline-none cursor-pointer border shadow-lg ${
                          isDark
                            ? "border-rose-500/40 bg-slate-900/95 text-rose-400 hover:bg-rose-500/20 hover:text-rose-300 hover:border-rose-400 hover:shadow-[0_0_14px_rgba(244,63,94,0.4)]"
                            : "border-rose-500/30 bg-white text-rose-600 hover:bg-rose-500/15 hover:text-rose-700 shadow-rose-500/10"
                        }`}
                        title="Attach Document or PDF (PDF, DOCX, TXT, Code)"
                        aria-label="Attach Document"
                      >
                        <FileIcon className="w-4.5 h-4.5" strokeWidth={1.8} />
                      </button>
                    </motion.div>

                    {/* Right Circle: Browse All Files */}
                    <motion.div
                      key="attach-all"
                      initial={{ opacity: 0, scale: 0.3, x: 0, y: 0 }}
                      animate={{ opacity: 1, scale: 1, x: 34, y: -28 }}
                      exit={{ opacity: 0, scale: 0.3, x: 0, y: 0 }}
                      transition={{ type: "spring", stiffness: 500, damping: 28, mass: 0.8, delay: 0.04 }}
                      className="absolute top-0 left-0 w-9 h-9 pointer-events-auto"
                    >
                      <button
                        type="button"
                        onClick={() => {
                          sfx.playClick?.();
                          openFileDialog("*/*");
                        }}
                        className={`w-9 h-9 rounded-full flex items-center justify-center transition-transform duration-150 hover:scale-110 active:scale-95 focus:outline-none cursor-pointer border shadow-lg ${
                          isDark
                            ? "border-violet-500/40 bg-slate-900/95 text-violet-400 hover:bg-violet-500/20 hover:text-violet-300 hover:border-violet-400 hover:shadow-[0_0_14px_rgba(167,139,250,0.4)]"
                            : "border-violet-500/30 bg-white text-violet-600 hover:bg-violet-500/15 hover:text-violet-700 shadow-violet-500/10"
                        }`}
                        title="Browse All Files (Any local file)"
                        aria-label="Browse All Files"
                      >
                        <Folder className="w-4.5 h-4.5" strokeWidth={1.8} />
                      </button>
                    </motion.div>
                  </>
                )}
              </AnimatePresence>

              {/* Trigger Button (+ morphs to X with theme gradient) */}
              <button
                id="attach-file-button"
                type="button"
                onClick={() => {
                  sfx.playClick?.();
                  setIsMenuOpen(!isMenuOpen);
                }}
                disabled={disabled || isListening || isTranscribing}
                className={`relative z-10 w-8 h-8 sm:w-9 sm:h-9 rounded-full flex items-center justify-center transition-all duration-200 active:scale-95 focus:outline-none cursor-pointer border ${
                  isMenuOpen
                    ? "border-transparent text-white shadow-lg"
                    : isDark
                    ? "border-white/15 text-slate-200 hover:text-white hover:bg-white/10"
                    : "border-black/10 text-slate-700 hover:text-black hover:bg-black/5"
                }`}
                style={
                  isMenuOpen
                    ? {
                        background: theme.gradient,
                        boxShadow: `0 0 16px ${theme.glow}`,
                      }
                    : undefined
                }
                title={isMenuOpen ? "Close attachment menu" : "Attach image, document, or files"}
                aria-label={isMenuOpen ? "Close attachment menu" : "Attach files"}
              >
                <Plus
                  className="w-4 h-4 transition-transform duration-200 ease-out"
                  style={{
                    transform: isMenuOpen ? "rotate(45deg)" : "rotate(0deg)",
                  }}
                  strokeWidth={2}
                />
              </button>
            </div>
          }
          rightSlot={
            /* Action Group: Microphone + Submit Button */
            <div className="flex items-center space-x-1 sm:space-x-1.5 flex-shrink-0">
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
                  aria-label={isListening ? "Stop listening" : "Start voice listening"}
                  className={`relative w-8 h-8 sm:w-9 sm:h-9 rounded-full transition-all duration-300 flex items-center justify-center active:scale-95 hover:-translate-y-0.5 z-10 overflow-hidden border ${
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
                    <Loader2 className="w-3.5 h-3.5 animate-spin relative z-10 text-indigo-400" />
                  ) : (
                    <Mic className="w-3.5 h-3.5 relative z-10 transition-transform group-hover:scale-105" />
                  )}
                </button>
              </div>

              {/* Submit button */}
              <button
                id="send-command-button"
                type="submit"
                disabled={
                  disabled ||
                  (!inputText.trim() && !attachment) ||
                  isListening ||
                  isTranscribing ||
                  isAttachmentPending
                }
                aria-label="Send message"
                className={`w-8 h-8 sm:w-9 sm:h-9 rounded-full transition-all duration-200 active:scale-95 hover:-translate-y-0.5 flex items-center justify-center flex-shrink-0 ${
                  (inputText.trim() || attachment) && !isAttachmentPending
                    ? "text-white shadow-md cursor-pointer hover:brightness-110"
                    : isDark
                    ? "cursor-not-allowed border"
                    : "cursor-not-allowed border"
                }`}
                style={
                  (inputText.trim() || attachment) && !isAttachmentPending
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
                title={
                  isAttachmentPending
                    ? "Please wait for document indexing to complete"
                    : attachment
                    ? "Send message with attachment (Enter)"
                    : "Send message (Enter)"
                }
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </div>
          }
        />
      </form>
    );
  })()}
      {/* Live region for accessibility assistive tech */}
      <div role="status" aria-live="polite" className="sr-only">
        {isListening ? (liveTranscript || "Listening for speech...") : ""}
      </div>
    </div>
  );
};
