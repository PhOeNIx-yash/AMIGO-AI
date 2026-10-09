import React, { useState, useEffect, useRef, useMemo } from "react";
import {
  Play,
  Square,
  CheckCircle2,
  Check,
} from "lucide-react";
import { sfx } from "../utils/audio";
import { audioBus } from "../utils/audioBus";
import { saveAssistantSettings } from "../services/assistantApi";

export interface VoiceOption {
  id: string;
  name: string;
  engine: "Supertonic 3";
  gender: "Female" | "Male" | "Robot";
  accent: "US" | "GB";
  desc: string;
  previewText: string;
  avatarGradient: string;
  color?: string;
}

import FluidOrb from "../../components/ui/fluid-orb";

export const VOICES_CATALOG: VoiceOption[] = [
  // 5 Best Studio Female Neural Voices (Supertonic 3 - 44.1kHz)
  {
    id: "nova",
    name: "Nova (F1)",
    engine: "Supertonic 3",
    gender: "Female",
    accent: "US",
    desc: "Smooth, articulate & studio-clean neural female (Recommended)",
    previewText: "Hello! I am Nova, your clear and articulate studio voice.",
    avatarGradient: "from-rose-500 to-pink-600",
    color: "#f43f5e",
  },
  {
    id: "aria",
    name: "Aria (F2)",
    engine: "Supertonic 3",
    gender: "Female",
    accent: "US",
    desc: "Soft, natural & warm conversational female delivery",
    previewText: "Hello! I am Aria, soft, warm and conversational.",
    avatarGradient: "from-fuchsia-500 to-rose-600",
    color: "#ec4899",
  },
  {
    id: "serena",
    name: "Serena (F3)",
    engine: "Supertonic 3",
    gender: "Female",
    accent: "US",
    desc: "Warm, expressive and friendly conversational tone",
    previewText: "Hi there! I am Serena, warm, expressive and natural.",
    avatarGradient: "from-violet-500 to-purple-600",
    color: "#8b5cf6",
  },
  {
    id: "chloe",
    name: "Chloe (F4)",
    engine: "Supertonic 3",
    gender: "Female",
    accent: "US",
    desc: "Bright, friendly & clear neural female delivery",
    previewText: "Hey! I am Chloe, bright, friendly and clear.",
    avatarGradient: "from-sky-400 to-blue-500",
    color: "#0ea5e9",
  },
  {
    id: "luna",
    name: "Luna (F5)",
    engine: "Supertonic 3",
    gender: "Female",
    accent: "US",
    desc: "Crisp, energetic and articulate female delivery",
    previewText: "Hi there! I am Luna, crisp, energetic and ready to help.",
    avatarGradient: "from-amber-400 to-rose-500",
    color: "#f59e0b",
  },

  // 5 Best Studio Male Neural Voices (Supertonic 3 - 44.1kHz)
  {
    id: "orion",
    name: "Orion (M1)",
    engine: "Supertonic 3",
    gender: "Male",
    accent: "US",
    desc: "Deep, calm and grounded baritone resonance",
    previewText: "Hello, I am Orion. Deep, calm and ready to assist you.",
    avatarGradient: "from-blue-600 to-indigo-700",
    color: "#3b82f6",
  },
  {
    id: "atlas",
    name: "Atlas (M2)",
    engine: "Supertonic 3",
    gender: "Male",
    accent: "US",
    desc: "Professional, crisp and articulate executive tone",
    previewText: "Greetings! I am Atlas. Professional, clear and articulate.",
    avatarGradient: "from-cyan-600 to-blue-700",
    color: "#06b6d4",
  },
  {
    id: "leo",
    name: "Leo (M3)",
    engine: "Supertonic 3",
    gender: "Male",
    accent: "US",
    desc: "Warm, relatable and conversational companion",
    previewText: "Hey there! I am Leo, warm and easy to talk to.",
    avatarGradient: "from-emerald-500 to-teal-700",
    color: "#10b981",
  },
  {
    id: "felix",
    name: "Felix (M4)",
    engine: "Supertonic 3",
    gender: "Male",
    accent: "US",
    desc: "Young, natural, and modern male delivery",
    previewText: "Hey! I am Felix, young, natural and fast.",
    avatarGradient: "from-teal-500 to-cyan-600",
    color: "#14b8a6",
  },
  {
    id: "ethan",
    name: "Ethan (M5)",
    engine: "Supertonic 3",
    gender: "Male",
    accent: "US",
    desc: "Distinguished, articulate gentleman delivery",
    previewText: "Good day! I am Ethan, speaking distinguished studio English.",
    avatarGradient: "from-amber-600 to-orange-700",
    color: "#ea580c",
  },
];

interface VoiceSettingsTabProps {
  isDark: boolean;
  theme: any;
  selectedVoice: string;
  setSelectedVoice: (voiceId: string) => void;
  playingVoiceId: string | null;
  setPlayingVoiceId: (voiceId: string | null) => void;
  voiceFilter: "all" | "female" | "male";
  setVoiceFilter: (filter: "all" | "female" | "male") => void;
}

export const VoiceSettingsTab: React.FC<VoiceSettingsTabProps> = ({
  isDark,
  theme,
  selectedVoice,
  setSelectedVoice,
  playingVoiceId,
  setPlayingVoiceId,
  voiceFilter,
  setVoiceFilter,
}) => {
  const [voiceFilterLocal, setVoiceFilterLocal] = useState<"all" | "female" | "male">(voiceFilter);
  const [playingVoiceIdLocal, setPlayingVoiceIdLocal] = useState<string | null>(playingVoiceId);
  const previewTimerRef = useRef<any>(null);
  const savedTimerRef = useRef<any>(null);
  const [savedBanner, setSavedBanner] = useState(false);

  useEffect(() => {
    return () => {
      if (previewTimerRef.current) {
        clearTimeout(previewTimerRef.current);
        previewTimerRef.current = null;
      }
      if (savedTimerRef.current) {
        clearTimeout(savedTimerRef.current);
        savedTimerRef.current = null;
      }
      try {
        fetch("/api/action/execute", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ payload: { tool: "stop_speaking" } }),
        }).catch(() => {});
      } catch {}
    };
  }, []);

  useEffect(() => {
    setVoiceFilterLocal(voiceFilter);
    setPlayingVoiceIdLocal(playingVoiceId);
  }, [voiceFilter, playingVoiceId]);

  const handleSelectVoice = async (voiceId: string) => {
    setSelectedVoice(voiceId);
    try {
      localStorage.setItem("amigo_selected_voice", voiceId);
    } catch (_) {}
    try {
      await saveAssistantSettings({
        user_profile: { preferences: { voice: voiceId } },
        voice: voiceId,
      });
      if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
      setSavedBanner(true);
      savedTimerRef.current = setTimeout(() => setSavedBanner(false), 2000);

      const vObj = VOICES_CATALOG.find((v) => v.id === voiceId);
      const name = vObj ? vObj.name : voiceId;
      await fetch("/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: `${name} voice selected.`, voice: voiceId }),
      });
    } catch (e) {
      console.warn("Failed to set voice:", e);
    }
  };

  const handleTogglePlayPreview = async (e: React.MouseEvent, voice: VoiceOption) => {
    e.stopPropagation();
    if (previewTimerRef.current) {
      clearTimeout(previewTimerRef.current);
      previewTimerRef.current = null;
    }

    if (playingVoiceIdLocal === voice.id) {
      setPlayingVoiceIdLocal(null);
      setPlayingVoiceId(null);
      try {
        await fetch("/api/action/execute", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ payload: { tool: "stop_speaking" } }),
        });
      } catch {}
      return;
    }

    // Stop any existing speaking audio before starting a new preview so audio does not overlap
    try {
      await fetch("/api/action/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload: { tool: "stop_speaking" } }),
      });
    } catch {}

    setPlayingVoiceIdLocal(voice.id);
    setPlayingVoiceId(voice.id);

    try {
      // Synthesize & play preview without mutating the user's saved voice settings
      const res = await fetch("/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: voice.previewText, voice: voice.id }),
      });
      if (!res.ok) {
        setPlayingVoiceIdLocal(null);
        setPlayingVoiceId(null);
        return;
      }

      // Reset animation once utterance finishes
      const durationMs = Math.max(3000, voice.previewText.length * 80);
      previewTimerRef.current = setTimeout(() => {
        setPlayingVoiceIdLocal(null);
        setPlayingVoiceId(null);
        previewTimerRef.current = null;
      }, durationMs);
    } catch (err) {
      setPlayingVoiceIdLocal(null);
      setPlayingVoiceId(null);
    }
  };

  const filteredVoices = useMemo(() => {
    if (voiceFilterLocal === "female") {
      return VOICES_CATALOG.filter((v) => v.gender === "Female");
    }
    if (voiceFilterLocal === "male") {
      return VOICES_CATALOG.filter((v) => v.gender === "Male");
    }
    return VOICES_CATALOG;
  }, [voiceFilterLocal]);

  return (
    <div className="space-y-4">
      {/* Active Voice Spotlight Banner */}
      {(() => {
        const currentVoiceId = playingVoiceIdLocal || selectedVoice;
        const activeVoice = VOICES_CATALOG.find((v) => v.id === currentVoiceId) || VOICES_CATALOG[0];
        const isSpeakingActive = Boolean(playingVoiceIdLocal);
        return (
          <div
            className={`p-4 rounded-2xl border relative overflow-hidden transition-all ${
              isDark ? "bg-white/[0.03]" : "bg-white shadow-sm"
            }`}
            style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-3.5">
                <div className="relative flex items-center justify-center p-1 flex-shrink-0">
                  <FluidOrb
                    size={52}
                    color={activeVoice.color || theme.primary}
                    isSpeaking={isSpeakingActive}
                  />
                </div>
                <div>
                  <div className="flex items-center space-x-2">
                    <span className="text-sm font-semibold tracking-tight">{activeVoice.name}</span>
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                        activeVoice.engine === "Supertonic 3"
                          ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/20"
                          : "bg-blue-500/15 text-blue-400 border border-blue-500/20"
                      }`}
                    >
                      {activeVoice.engine} 44.1kHz
                    </span>
                    <span
                      className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-mono border"
                      style={{
                        backgroundColor: `${theme.primary}18`,
                        color: isDark ? (theme.accent || theme.primary) : theme.primary,
                        borderColor: `${theme.primary}30`,
                      }}
                    >
                      {playingVoiceIdLocal ? "Previewing" : "Active Voice"}
                    </span>
                  </div>
                  <p className="text-xs opacity-60 mt-0.5">{activeVoice.desc}</p>
                </div>
              </div>

              <button
                type="button"
                onClick={(e) => handleTogglePlayPreview(e, activeVoice)}
                className={`p-2.5 rounded-xl border transition-all flex items-center justify-center ${
                  playingVoiceIdLocal === activeVoice.id
                    ? "bg-rose-500/20 border-rose-500/40 text-rose-300"
                    : isDark
                    ? "bg-white/5 border-white/10 hover:bg-white/10 text-white/80"
                    : "bg-black/5 border-black/10 hover:bg-black/10 text-slate-700"
                }`}
                title={playingVoiceIdLocal === activeVoice.id ? "Stop Preview" : "Preview Sample"}
              >
                {playingVoiceIdLocal === activeVoice.id ? (
                  <Square className="w-4 h-4 fill-current" />
                ) : (
                  <Play className="w-4 h-4 fill-current" />
                )}
              </button>
            </div>
          </div>
        );
      })()}

      {/* Engine / Category Filter Segmented Tabs */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 pt-1">
        <div className="text-xs font-semibold uppercase tracking-wider opacity-60">Studio Neural Voices (44.1kHz)</div>
        <div
          role="radiogroup"
          aria-label="Filter voices by gender"
          className={`inline-flex p-1 rounded-xl border self-start sm:self-auto ${
            isDark ? "bg-black/30" : "bg-slate-100"
          }`}
          style={{ borderColor: isDark ? `${theme.primary}18` : `${theme.primary}12` }}
        >
          {(
            [
              { id: "all", label: `All (${VOICES_CATALOG.length})` },
              { id: "female", label: `Female (${VOICES_CATALOG.filter((v) => v.gender === "Female").length})` },
              { id: "male", label: `Male (${VOICES_CATALOG.filter((v) => v.gender === "Male").length})` },
            ] as const
          ).map((f) => (
            <button
              key={f.id}
              type="button"
              role="radio"
              aria-checked={voiceFilterLocal === f.id}
              onClick={() => {
                setVoiceFilter(f.id);
                setVoiceFilterLocal(f.id);
              }}
              className={`px-3 py-1 rounded-lg text-[11px] font-medium transition-all ${
                voiceFilterLocal === f.id
                  ? isDark
                    ? "bg-white/15 text-white font-semibold shadow-sm"
                    : "bg-white text-slate-900 font-semibold shadow-sm"
                  : "opacity-60 hover:opacity-100"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Voice Selection Cards Grid */}
      <div role="radiogroup" aria-label="Select Assistant Voice" className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
        {filteredVoices.map((voice) => {
          const isSelected = selectedVoice === voice.id;
          const isPlaying = playingVoiceIdLocal === voice.id;

          return (
            <div
              key={voice.id}
              role="radio"
              aria-checked={isSelected}
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  handleSelectVoice(voice.id);
                }
              }}
              onClick={() => handleSelectVoice(voice.id)}
              className={`group relative p-3.5 rounded-2xl border text-left transition-all cursor-pointer select-none flex flex-col justify-between ${
                isSelected
                  ? "shadow-sm"
                  : isDark
                  ? "border-white/10 bg-white/[0.02] hover:bg-white/[0.05] hover:border-white/20"
                  : "border-black/10 bg-white hover:bg-slate-50 hover:border-black/20 shadow-sm"
              }`}
              style={
                isSelected
                  ? {
                      borderColor: `${theme.primary}80`,
                      backgroundColor: `${theme.primary}10`,
                      boxShadow: `0 0 0 1px ${theme.primary}30`,
                    }
                  : {}
              }
            >
              <div>
                <div className="flex items-start justify-between mb-2.5">
                  <div className="flex items-center space-x-2.5">
                    <div className="relative flex items-center justify-center p-0.5 flex-shrink-0">
                      <FluidOrb
                        size={36}
                        color={voice.color || theme.primary}
                        isSpeaking={isPlaying}
                      />
                    </div>
                    <div>
                      <div className="text-xs font-semibold tracking-tight flex items-center space-x-1.5">
                        <span>{voice.name}</span>
                        {isSelected && (
                          <CheckCircle2
                            className="w-3.5 h-3.5"
                            style={{ color: theme.accent || theme.primary }}
                          />
                        )}
                      </div>
                      <div className="text-[10px] opacity-60">
                        {voice.gender} • {voice.accent}
                      </div>
                    </div>
                  </div>

                  <span
                    className="text-[9px] font-mono px-2 py-0.5 rounded-full font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/20"
                  >
                    Supertonic 3 44.1kHz
                  </span>
                </div>

                <p className="text-[11px] opacity-70 leading-relaxed mb-3">{voice.desc}</p>
              </div>

              {/* Bottom Actions Row */}
              <div className="flex items-center justify-between pt-2 border-t border-white/5">
                <button
                  type="button"
                  onClick={(e) => handleTogglePlayPreview(e, voice)}
                  className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all ${
                    isPlaying
                      ? "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                      : isDark
                      ? "bg-white/5 hover:bg-white/10 text-white/80 border border-white/10"
                      : "bg-black/5 hover:bg-black/10 text-slate-700 border border-black/10"
                  }`}
                >
                  {isPlaying ? (
                    <>
                      <Square className="w-3 h-3 fill-current" />
                      <span>Stop</span>
                    </>
                  ) : (
                    <>
                      <Play className="w-3 h-3 fill-current" />
                      <span>Preview</span>
                    </>
                  )}
                </button>

                <span
                  className={`text-[10px] font-medium ${
                    isSelected ? "font-semibold" : "opacity-40"
                  }`}
                  style={isSelected ? { color: isDark ? (theme.accent || theme.primary) : theme.primary } : {}}
                >
                  {isSelected ? "Selected" : "Click to select"}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {savedBanner && (
        <div className="fixed inset-0 flex items-center justify-center pointer-events-none">
          <div className="text-xs font-semibold text-emerald-400 flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/20 animate-fade-in">
            <Check className="w-3.5 h-3.5" />
            <span>Saved</span>
          </div>
        </div>
      )}
    </div>
  );
};