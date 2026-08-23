import React, { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Mic, Send, Loader2, Square, Radio, Sparkles } from "lucide-react";
import { sfx } from "../utils/audio";
import { transcribeAudio } from "../services/assistantApi";
import { BackendConfig, ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { KineticTranscript } from "./KineticText";
import { audioBus } from "../utils/audioBus";

interface VoiceControlsProps {
  onProcessCommand: (prompt: string) => void;
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
    if (!inputText.trim() || disabled || isListening || isTranscribing) return;
    sfx.playSubmit();
    const command = inputText.trim();
    setInputText("");
    onProcessCommand(command);
  };

  return (
    <div className="w-full max-w-2xl mx-auto flex flex-col items-center px-3 pb-3 sm:pb-4">

      {/* Search / Voice input fluid capsule */}
      <div
        className={`w-full flex items-center p-1.5 sm:p-2 rounded-2xl sm:rounded-full backdrop-blur-2xl border transition-all duration-300 ${
          isListening
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
          backgroundColor: isDark
            ? `${theme.primary}18`
            : "rgba(255, 255, 255, 0.92)",
          boxShadow: isListening
            ? `0 0 35px ${theme.glow}, 0 12px 30px rgba(0,0,0,0.35)`
            : isFocused
            ? `0 0 28px ${theme.glow}, 0 8px 24px rgba(0,0,0,0.25)`
            : isDark
            ? `0 8px 30px rgba(0,0,0,0.35), 0 0 22px ${theme.glow}, inset 0 1px 0 rgba(255,255,255,0.15)`
            : `0 8px 25px rgba(0,0,0,0.06), 0 0 18px ${theme.glow}40, inset 0 1px 0 rgba(255,255,255,0.9)`,
          borderColor: isListening || isFocused
            ? (theme.accent || theme.primary)
            : `${theme.primary}66`,
        }}
      >
        {/* Form and Text / Live transcript container */}
        <form
          onSubmit={handleSubmitText}
          autoComplete="off"
          className="flex-1 flex items-center min-w-0"
        >
          <div className="relative flex-1 min-w-0">
            {isListening ? (
              <div className="flex items-center justify-between py-2 px-3 sm:px-4 min-h-[44px]">
                <div className="flex-1 min-w-0 pr-3">
                  {liveTranscript ? (
                    <KineticTranscript
                      text={liveTranscript}
                      isDark={isDark}
                      colorTheme={colorTheme}
                      isLive={true}
                    />
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
                  disabled={disabled || isListening || isTranscribing}
                  placeholder="Ask anything or use voice commands..."
                  className={`w-full py-2.5 sm:py-3 pl-3.5 sm:pl-5 pr-3 text-xs sm:text-sm font-normal bg-transparent focus:outline-none transition-all text-input-glowing-cursor ${
                    isDark
                      ? "text-slate-100 placeholder:text-slate-400"
                      : "text-slate-900 placeholder:text-slate-400"
                  }`}
                  style={{
                    caretColor: theme.accent || theme.primary,
                  }}
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
                className={`relative w-9 h-9 sm:w-10 sm:h-10 rounded-full transition-all duration-300 flex items-center justify-center active:scale-95 z-10 overflow-hidden border ${
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
              disabled={disabled || !inputText.trim() || isListening || isTranscribing}
              className={`w-9 h-9 sm:w-10 sm:h-10 rounded-full transition-all duration-200 active:scale-95 flex items-center justify-center flex-shrink-0 ${
                inputText.trim()
                  ? "text-white shadow-md cursor-pointer hover:brightness-110"
                  : isDark
                  ? "cursor-not-allowed border"
                  : "cursor-not-allowed border"
              }`}
              style={
                inputText.trim()
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
              title="Send message (Enter)"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
