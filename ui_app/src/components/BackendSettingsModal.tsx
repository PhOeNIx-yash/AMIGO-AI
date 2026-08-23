import React, { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Server,
  X,
  CheckCircle2,
  AlertCircle,
  Activity,
  Key,
  Globe,
  Radio,
  Sliders,
  Code2,
  RotateCcw,
  Sparkles,
  ExternalLink,
} from "lucide-react";
import { BackendConfig, ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { testBackendConnection } from "../services/assistantApi";
import { sfx } from "../utils/audio";

interface BackendSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  config: BackendConfig;
  onSaveConfig: (config: BackendConfig) => void;
  isDark: boolean;
  colorTheme?: ColorTheme;
}

export const BackendSettingsModal: React.FC<BackendSettingsModalProps> = ({
  isOpen,
  onClose,
  config,
  onSaveConfig,
  isDark,
  colorTheme = "violet",
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.violet;
  const [endpointUrl, setEndpointUrl] = useState(config.endpointUrl || "");
  const [actionWebhookUrl, setActionWebhookUrl] = useState(config.actionWebhookUrl || "");
  const [apiKey, setApiKey] = useState(config.apiKey || "");
  const [customHeaders, setCustomHeaders] = useState(config.customHeaders || "");
  const [transcriptionEngine, setTranscriptionEngine] = useState(config.transcriptionEngine || "web-speech");
  const [autoSpeech, setAutoSpeech] = useState(config.autoSpeech !== false);

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success?: boolean;
    latencyMs?: number;
    message?: string;
  } | null>(null);

  const [showDocs, setShowDocs] = useState(false);

  const handleTest = async () => {
    sfx.playClick();
    setTesting(true);
    setTestResult(null);

    const testConf: BackendConfig = {
      endpointUrl,
      actionWebhookUrl,
      apiKey,
      customHeaders,
      protocol: "rest",
      autoSpeech,
      transcriptionEngine,
    };

    const result = await testBackendConnection(testConf);
    setTestResult(result);
    setTesting(false);
    if (result.success) {
      sfx.playSuccess();
    }
  };

  const handleSave = () => {
    sfx.playSuccess();
    const updated: BackendConfig = {
      endpointUrl: endpointUrl.trim(),
      actionWebhookUrl: actionWebhookUrl.trim() || undefined,
      apiKey: apiKey.trim() || undefined,
      customHeaders: customHeaders.trim() || undefined,
      protocol: "rest",
      autoSpeech,
      transcriptionEngine,
    };
    onSaveConfig(updated);
    onClose();
  };

  const handleResetDefaults = () => {
    sfx.playClick();
    setEndpointUrl("/api/assistant/process");
    setActionWebhookUrl("");
    setApiKey("");
    setCustomHeaders("");
    setTranscriptionEngine("gemini-3.5-flash");
    setAutoSpeech(true);
    setTestResult(null);
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/60 backdrop-blur-md"
          />

          {/* Modal Card */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 15 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 15 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className={`relative w-full max-w-xl rounded-2xl border shadow-2xl overflow-hidden flex flex-col max-h-[90vh] z-10 ${
              isDark
                ? "bg-[#11111d] border-white/10 text-slate-100 shadow-indigo-950/50"
                : "bg-white border-black/10 text-slate-900 shadow-indigo-200/50"
            }`}
          >
            {/* Header */}
            <div
              className={`flex items-center justify-between px-5 py-4 border-b ${
                isDark ? "border-white/10 bg-white/5" : "border-black/5 bg-slate-50"
              }`}
            >
              <div className="flex items-center space-x-3">
                <div
                  className="w-8 h-8 rounded-xl flex items-center justify-center text-white shadow-md"
                  style={{ background: theme.gradient }}
                >
                  <Server className="w-4 h-4" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold tracking-tight">Plug-and-Play Backend Connector</h2>
                  <p className="text-xs opacity-60">Connect your custom API, agent, or FastAPI/Flask/Express service</p>
                </div>
              </div>

              <button
                onClick={onClose}
                className="p-1.5 rounded-lg opacity-60 hover:opacity-100 hover:bg-black/5 dark:hover:bg-white/10 transition-all"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Body */}
            <div className="p-5 space-y-4 overflow-y-auto custom-scrollbar flex-1 text-xs sm:text-sm">
              {/* Endpoint URL Input */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider mb-1.5 opacity-80">
                  Assistant Process Endpoint URL
                </label>
                <div className="relative">
                  <Globe className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 opacity-40" />
                  <input
                    type="text"
                    value={endpointUrl}
                    onChange={(e) => setEndpointUrl(e.target.value)}
                    placeholder="e.g. http://localhost:8000/api/assistant or /api/assistant/process"
                    className={`w-full pl-9 pr-3 py-2 rounded-xl border text-xs sm:text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/50 font-mono transition-all ${
                      isDark
                        ? "bg-white/5 border-white/10 text-white placeholder:text-slate-500"
                        : "bg-black/5 border-black/10 text-slate-900 placeholder:text-slate-400"
                    }`}
                  />
                </div>
                <p className="text-[11px] opacity-50 mt-1">
                  Receives POST <code className="font-mono">{"{ prompt: string, context?: object }"}</code> and returns response JSON.
                </p>
              </div>

              {/* Action Webhook URL */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider mb-1.5 opacity-80">
                  Intent Action Callback Webhook (Optional)
                </label>
                <div className="relative">
                  <Radio className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 opacity-40" />
                  <input
                    type="text"
                    value={actionWebhookUrl}
                    onChange={(e) => setActionWebhookUrl(e.target.value)}
                    placeholder="e.g. http://localhost:8000/api/assistant/action"
                    className={`w-full pl-9 pr-3 py-2 rounded-xl border text-xs sm:text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/50 font-mono transition-all ${
                      isDark
                        ? "bg-white/5 border-white/10 text-white placeholder:text-slate-500"
                        : "bg-black/5 border-black/10 text-slate-900 placeholder:text-slate-400"
                    }`}
                  />
                </div>
                <p className="text-[11px] opacity-50 mt-1">
                  Called when user confirms or interacts with an intent action card.
                </p>
              </div>

              {/* Auth API Key */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider mb-1.5 opacity-80">
                  Authorization Bearer Token / API Key (Optional)
                </label>
                <div className="relative">
                  <Key className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 opacity-40" />
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="e.g. sk-backend-token-..."
                    className={`w-full pl-9 pr-3 py-2 rounded-xl border text-xs sm:text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/50 font-mono transition-all ${
                      isDark
                        ? "bg-white/5 border-white/10 text-white placeholder:text-slate-500"
                        : "bg-black/5 border-black/10 text-slate-900 placeholder:text-slate-400"
                    }`}
                  />
                </div>
              </div>

              {/* Custom Headers */}
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider mb-1.5 opacity-80">
                  Custom Headers JSON (Optional)
                </label>
                <textarea
                  rows={2}
                  value={customHeaders}
                  onChange={(e) => setCustomHeaders(e.target.value)}
                  placeholder='e.g. { "X-Session-ID": "user-123", "X-Custom-Env": "production" }'
                  className={`w-full p-2.5 rounded-xl border text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500/50 font-mono transition-all ${
                    isDark
                      ? "bg-white/5 border-white/10 text-white placeholder:text-slate-500"
                      : "bg-black/5 border-black/10 text-slate-900 placeholder:text-slate-400"
                  }`}
                />
              </div>

              {/* Speech Recognition Engine */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                <div>
                  <label className="block text-xs font-semibold uppercase tracking-wider mb-1.5 opacity-80">
                    Voice Transcription STT
                  </label>
                  <select
                    value={transcriptionEngine}
                    onChange={(e) => setTranscriptionEngine(e.target.value as any)}
                    className={`w-full p-2 rounded-xl border text-xs font-medium focus:outline-none focus:ring-2 focus:ring-indigo-500/50 ${
                      isDark ? "bg-[#181829] border-white/10 text-white" : "bg-white border-black/10 text-slate-900"
                    }`}
                  >
                    <option value="amigo-speech">Amigo Local Audio STT</option>
                    <option value="web-speech">Browser Web Speech API</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-semibold uppercase tracking-wider mb-1.5 opacity-80">
                    Voice Output TTS
                  </label>
                  <button
                    type="button"
                    onClick={() => setAutoSpeech(!autoSpeech)}
                    className={`w-full p-2 rounded-xl border text-xs font-medium flex items-center justify-between transition-all ${
                      autoSpeech
                        ? "bg-indigo-600/20 border-indigo-500/40 text-indigo-400"
                        : isDark
                        ? "bg-white/5 border-white/10 opacity-60"
                        : "bg-black/5 border-black/10 opacity-60"
                    }`}
                  >
                    <span>Auto-speak responses</span>
                    <span className="font-semibold">{autoSpeech ? "ON" : "OFF"}</span>
                  </button>
                </div>
              </div>

              {/* Test Connection Result Box */}
              {testResult && (
                <motion.div
                  initial={{ opacity: 0, y: 5 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={`p-3 rounded-xl border flex items-start space-x-2.5 text-xs ${
                    testResult.success
                      ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                      : "bg-rose-500/10 border-rose-500/30 text-rose-400"
                  }`}
                >
                  {testResult.success ? (
                    <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                  ) : (
                    <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold">{testResult.message}</div>
                    {testResult.latencyMs !== undefined && (
                      <div className="opacity-80 text-[11px] mt-0.5">Roundtrip ping: {testResult.latencyMs}ms</div>
                    )}
                  </div>
                </motion.div>
              )}

              {/* Expandable JSON Schema Specs */}
              <div className="border-t border-white/10 dark:border-white/5 pt-3">
                <button
                  type="button"
                  onClick={() => setShowDocs(!showDocs)}
                  className="flex items-center space-x-1.5 text-xs text-indigo-400 hover:text-indigo-300 font-medium"
                >
                  <Code2 className="w-3.5 h-3.5" />
                  <span>{showDocs ? "Hide" : "View"} Backend Response Format Schema</span>
                </button>

                {showDocs && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    className="mt-2 p-3 rounded-xl bg-black/40 border border-white/10 font-mono text-[11px] space-y-2 text-slate-300 overflow-x-auto"
                  >
                    <p className="text-indigo-300 font-semibold">// Example Backend Response JSON:</p>
                    <pre className="text-slate-400">
{`{
  "speechReply": "I found 3 emails and scheduled your meeting.",
  "displayTitle": "Schedule meeting with team",
  "intent": "schedule_meeting",
  "actionCards": [
    {
      "id": "act-1",
      "type": "calendar",
      "title": "Team Sync at 3:00 PM",
      "subtitle": "Room 4B • 4 attendees",
      "selected": true
    }
  ],
  "executionSummary": {
    "status": "completed",
    "headline": "Meeting Scheduled",
    "details": "Invitation sent to engineering team."
  }
}`}
                    </pre>
                    <p className="text-[10px] text-slate-400">
                      *Note: The frontend also automatically adapts raw text or custom agent responses!
                    </p>
                  </motion.div>
                )}
              </div>
            </div>

            {/* Footer Buttons */}
            <div
              className={`p-4 border-t flex items-center justify-between space-x-2 ${
                isDark ? "border-white/10 bg-white/5" : "border-black/5 bg-slate-50"
              }`}
            >
              <button
                type="button"
                onClick={handleResetDefaults}
                className="flex items-center space-x-1 text-xs opacity-60 hover:opacity-100 transition-opacity px-2 py-1"
                title="Reset to default local endpoint"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Reset</span>
              </button>

              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  onClick={handleTest}
                  disabled={testing}
                  className={`px-3 py-2 rounded-xl text-xs font-semibold border flex items-center space-x-1.5 transition-all ${
                    isDark
                      ? "bg-white/5 hover:bg-white/10 border-white/10 text-slate-200"
                      : "bg-black/5 hover:bg-black/10 border-black/10 text-slate-800"
                  }`}
                >
                  <Activity className={`w-3.5 h-3.5 ${testing ? "animate-spin text-indigo-400" : ""}`} />
                  <span>{testing ? "Testing..." : "Test Ping"}</span>
                </button>

                <button
                  type="button"
                  onClick={handleSave}
                  className="px-4 py-2 rounded-xl text-xs font-semibold text-white shadow-md transition-all active:scale-95"
                  style={{ background: theme.gradient, boxShadow: `0 4px 14px ${theme.glow}` }}
                >
                  Save & Connect
                </button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
};
