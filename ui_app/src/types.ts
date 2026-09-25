export type VisualizerMode = "ribbon" | "orb";

export type AssistantState =
  | "idle" // "What can I help you with ?"
  | "listening" // Active microphone listening with voice waves
  | "processing" // Analyzing command / typing text
  | "action_card" // Showing action choices
  | "contact_picker" // Disambiguation / choice picker
  | "working" // Executing task in background
  | "completed"; // Final result card / dispatch summary

export type ColorTheme = "violet" | "cobalt" | "emerald" | "sunset" | "cyan" | "weather" | "noir";

export type PluginMode = "floating" | "docked_right" | "docked_bottom" | "fullscreen";

export interface ActionCardItem {
  id: string;
  type: "message" | "restaurant" | "map" | "calendar" | "general" | "link" | "device" | "code" | "confirmation" | "weather" | "timer" | "stopwatch" | "media" | "file";
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
  params?: Record<string, any>;
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
  url?: string;
  metadata?: Record<string, any>;
}

export interface HistoryEntry {
  id: string;
  timestamp: number;
  prompt: string;
  status?: string;
  response: AssistantResponse;
  selectedContact?: ContactItem;
}

export interface BackendConfig {
  endpointUrl: string;
  actionWebhookUrl?: string;
  apiKey?: string;
  customHeaders?: string;
  protocol: "rest" | "websocket";
  autoSpeech: boolean;
  transcriptionEngine: "amigo-speech" | "web-speech" | "custom";
  transcriptionUrl?: string;
  thinkingEnabled?: boolean;
}

export interface AttachmentItem {
  id: string;
  file?: File;
  filename: string;
  fileType: "pdf" | "image" | "document" | "generic";
  size: number;
  previewUrl?: string;
  uploadStatus: "idle" | "uploading" | "success" | "error";
  filepath?: string;
  extractedPreview?: string;
  error?: string;
}

