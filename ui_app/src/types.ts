export type VisualizerMode = "ribbon" | "orb";

export type AssistantState =
  | "idle" // "What can I help you with ?"
  | "listening" // Active microphone listening with voice waves
  | "processing" // Analyzing command / typing text
  | "action_card" // Showing action choices
  | "contact_picker" // Disambiguation / choice picker
  | "working" // Executing task in background
  | "completed"; // Final result card / dispatch summary

export type ColorTheme = "violet" | "cobalt" | "emerald" | "sunset" | "cyan" | "weather";

export type PluginMode = "floating" | "docked_right" | "docked_bottom" | "fullscreen";

export interface ActionCardItem {
  id: string;
  type: "message" | "restaurant" | "map" | "calendar" | "general" | "link" | "device" | "code" | "confirmation" | "weather" | "timer" | "stopwatch";
  title: string;
  subtitle?: string;
  selected: boolean;
  payload?: any;
  url?: string;
  badge?: string;
  actionType?: "toggle" | "button" | "navigate" | "execute";
}

export interface ContactItem {
  id: string;
  name: string;
  handle: string;
  avatarColor: string;
  avatarImg?: string;
  role?: string;
}

export interface AssistantResponse {
  speechReply: string;
  displayTitle: string;
  intent: string;
  requiresDisambiguation: boolean;
  disambiguationQuestion?: string;
  actionCards: ActionCardItem[];
  contactMatches?: ContactItem[];
  executionSummary: {
    status: string;
    headline: string;
    details: string;
    secondaryDetails?: string;
    rawOutput?: any;
  };
  metadata?: Record<string, any>;
}

export interface HistoryEntry {
  id: string;
  timestamp: number;
  prompt: string;
  response: AssistantResponse;
  selectedContact?: ContactItem;
}

export interface BackendConfig {
  endpointUrl: string;
  actionWebhookUrl?: string;
  apiKey?: string;
  customHeaders?: string; // JSON string of key-values
  protocol: "rest" | "websocket";
  autoSpeech: boolean;
  transcriptionEngine: "amigo-speech" | "web-speech" | "custom";
  transcriptionUrl?: string;
}

export interface PluginConfig {
  mode: PluginMode;
  autoListenOnOpen: boolean;
  transcriptionEngine: "amigo-speech" | "web-speech";
  backendUrl: string;
  opacity: number;
}
