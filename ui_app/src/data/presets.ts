import { ContactItem } from "../types";

export interface ColorThemeDefinition {
  name: string;
  primary: string;
  secondary: string;
  accent: string;
  glow: string;
  gradient: string;
  btnGradient: string;
  bgDark: string;
  bgLight: string;
  borderDark: string;
  borderLight: string;
  accentText: string;
}

export const COLOR_THEMES: Record<string, ColorThemeDefinition> = {
  violet: {
    name: "Cyber Violet",
    primary: "#7c3aed",
    secondary: "#3b82f6",
    accent: "#c084fc",
    glow: "rgba(124, 58, 237, 0.45)",
    gradient: "linear-gradient(135deg, #7c3aed 0%, #6366f1 50%, #3b82f6 100%)",
    btnGradient: "from-violet-600 via-indigo-600 to-blue-600 hover:from-violet-500 hover:to-blue-500",
    bgDark: "bg-[#0c0a18]",
    bgLight: "bg-[#f6f5fb]",
    borderDark: "border-violet-500/20",
    borderLight: "border-violet-500/20",
    accentText: "text-violet-400 dark:text-violet-300",
  },
  cobalt: {
    name: "Windows 11 Cobalt",
    primary: "#2563eb",
    secondary: "#06b6d4",
    accent: "#38bdf8",
    glow: "rgba(37, 99, 235, 0.45)",
    gradient: "linear-gradient(135deg, #2563eb 0%, #0284c7 50%, #06b6d4 100%)",
    btnGradient: "from-blue-600 via-sky-600 to-cyan-500 hover:from-blue-500 hover:to-cyan-400",
    bgDark: "bg-[#090d1a]",
    bgLight: "bg-[#f1f5fb]",
    borderDark: "border-blue-500/20",
    borderLight: "border-blue-500/20",
    accentText: "text-sky-400 dark:text-sky-300",
  },
  emerald: {
    name: "Aurora Emerald",
    primary: "#059669",
    secondary: "#0d9488",
    accent: "#34d399",
    glow: "rgba(5, 150, 105, 0.45)",
    gradient: "linear-gradient(135deg, #059669 0%, #0d9488 50%, #06b6d4 100%)",
    btnGradient: "from-emerald-600 via-teal-600 to-cyan-600 hover:from-emerald-500 hover:to-cyan-500",
    bgDark: "bg-[#07130f]",
    bgLight: "bg-[#f0f9f5]",
    borderDark: "border-emerald-500/20",
    borderLight: "border-emerald-500/20",
    accentText: "text-emerald-400 dark:text-emerald-300",
  },
  sunset: {
    name: "Nebula Amber",
    primary: "#d97706",
    secondary: "#e11d48",
    accent: "#fb923c",
    glow: "rgba(217, 119, 6, 0.45)",
    gradient: "linear-gradient(135deg, #d97706 0%, #ea580c 50%, #e11d48 100%)",
    btnGradient: "from-amber-600 via-orange-600 to-rose-600 hover:from-amber-500 hover:to-rose-500",
    bgDark: "bg-[#140d0a]",
    bgLight: "bg-[#faf6f1]",
    borderDark: "border-amber-500/20",
    borderLight: "border-amber-500/20",
    accentText: "text-amber-400 dark:text-amber-300",
  },
  cyan: {
    name: "Electric Cyan",
    primary: "#0891b2",
    secondary: "#4f46e5",
    accent: "#67e8f9",
    glow: "rgba(8, 145, 178, 0.45)",
    gradient: "linear-gradient(135deg, #0891b2 0%, #0284c7 50%, #4f46e5 100%)",
    btnGradient: "from-cyan-600 via-sky-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500",
    bgDark: "bg-[#061219]",
    bgLight: "bg-[#f0f8fa]",
    borderDark: "border-cyan-500/20",
    borderLight: "border-cyan-500/20",
    accentText: "text-cyan-400 dark:text-cyan-300",
  },
  weather: {
    name: "Atmospheric Sky",
    primary: "#0284c7",
    secondary: "#0ea5e9",
    accent: "#38bdf8",
    glow: "rgba(14, 165, 233, 0.45)",
    gradient: "linear-gradient(135deg, #0284c7 0%, #0ea5e9 40%, #38bdf8 75%, #f59e0b 100%)",
    btnGradient: "from-sky-600 via-cyan-600 to-amber-500 hover:from-sky-500 hover:to-amber-400",
    bgDark: "bg-[#061421]",
    bgLight: "bg-[#f0f8ff]",
    borderDark: "border-sky-500/20",
    borderLight: "border-sky-500/20",
    accentText: "text-sky-400 dark:text-sky-300",
  },
};

export interface GreetingPresetItem {
  id: string;
  text: string;
  category: "classic" | "amigo" | "creative" | "standby";
}

export const GREETING_PRESETS: GreetingPresetItem[] = [
  { id: "default", text: "What can I help you with ?", category: "classic" },
  { id: "assist_today", text: "How can I assist you today ?", category: "classic" },
  { id: "amigo_ready", text: "Amigo ready • What's on your mind ?", category: "amigo" },
  { id: "voice_standby", text: "Voice standby • Say a command", category: "standby" },
  { id: "ask_anything", text: "Ask Amigo anything or tap to speak", category: "creative" },
  { id: "accomplish_great", text: "Let's accomplish something great", category: "creative" },
  { id: "listening_cues", text: "Standing by • Listening for your cue", category: "standby" },
  { id: "get_things_done", text: "Good day ! Let's get things done", category: "amigo" },
  { id: "amigo_listening", text: "Hi, I'm Amigo • How can I help ?", category: "amigo" },
  { id: "ready_whenever", text: "Ready whenever you are", category: "creative" },
];

