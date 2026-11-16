# Amigo Voice & AI Assistant - Codebase Structure & Architecture Guide

> **Target Audience:** AI models, LLM agents, and human developers.  
> **Purpose:** Comprehensive architectural map, file-by-file reference, and execution pipelines of the Amigo Voice Assistant ecosystem.

---

## 1. System Overview & Architecture

Amigo is a hybrid local-first voice assistant and autonomous desktop agent for Windows. It combines local LLM inference (via `llama.cpp` GGUF), offline neural text-to-speech (Kokoro v1.0 ONNX), offline speech recognition (Vosk STT), vector-based Retrieval-Augmented Generation (RAG) with SQLite/Memvid memory, Windows OS automation, and a modern React/Vite web interface.

### High-Level Architecture Diagram

```
                ┌──────────────────────────────────────────────┐
                │             User Input Channels              │
                │   [Microphone]   [Web UI]   [Global Hotkey]  │
                └──────────────────────┬───────────────────────┘
                                       │
                                       ▼
                ┌──────────────────────────────────────────────┐
                │           Speech-To-Text Layer               │
                │        amigo/utils/tts.py (Vosk STT)         │
                └──────────────────────┬───────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          "FATHER" ORCHESTRATORS                             │
│                                                                             │
│   amigo/ui/server.py (Flask Web Server + SSE Broadcaster + API Hub)         │
│   scripts/amigo_main.py (Headless CLI + Live Audio Barge-In Loop)           │
└───────────────┬───────────────────────────────┬─────────────────────────────┘
                │                               │
                ▼                               ▼
┌───────────────────────────────┐   ┌─────────────────────────────────────────┐
│       Core LLM & Routing      │   │          Context & Memory Layer         │
│   amigo/core/llm_agent.py     │◄──┤  amigo/core/rag_engine.py (SQLite/BM25) │
│  (LFM3-Omni/Llama GGUF Engine)│   │  amigo/core/rag_indexer.py (File Scan)  │
└───────────────┬───────────────┘   │  amigo/core/proactive_intelligence.py   │
                │                   │  amigo/core/reminder_timer.py           │
                │ (Tool Dispatches) └─────────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Tool Registry & Action Services                       │
│                         amigo/utils/tool_registry.py                        │
│                                                                             │
│  ├─ OS Automation:      amigo/services/os_automation.py (Media, Volume)     │
│  ├─ OS Settings:        amigo/services/os_settings.py (WiFi, Bluetooth)     │
│  ├─ App Opener:         amigo/services/app_opener.py (Start Menu Scan)      │
│  ├─ Web Search:         amigo/services/web_search.py (DuckDuckGo, Scraper)  │
│  ├─ Weather:            amigo/services/weather.py (Open-Meteo)              │
│  ├─ Mail & Calendar:    amigo/services/mail_integration.py, calendar_...    │
│  ├─ Screen & Vision:    amigo/services/screen_vision.py, screen_pen.py      │
│  ├─ Content Generator:  amigo/services/content_generator.py (Drafts/Docs)   │
│  └─ Utilities:          amigo/utils/calculate.py, network_utils.py          │
└───────────────────────────────────────┬─────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Output Layer                                   │
│  ├─ Speech Output: Kokoro v1.0 ONNX Offline TTS (amigo/utils/tts.py)        │
│  ├─ Real-Time Web UI: React/Vite Dashboard via Server-Sent Events (SSE)    │
│  └─ Action Cards: Interactive UI widgets for confirmation & data preview    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Directory Tree

```
amigo-main/
├── amigo/                         # Core Python package
│   ├── config/                    # Configuration files
│   ├── core/                      # Intelligence, LLM, RAG, and timers
│   │   ├── llm_agent.py           # LLM loader, prompting, function router, memory
│   │   ├── proactive_intelligence.py # Autonomous background checks & reminders
│   │   ├── proactive_state.json   # State tracking for proactive suggestions
│   │   ├── rag_engine.py          # Vector search, document retrieval, SQLite DB
│   │   ├── rag_indexer.py         # Background worker indexing local user files
│   │   ├── reminder_timer.py      # Timers, reminders, alarms scheduler
│   │   └── amigo_reminders.json   # Persisted reminders and alarm data
│   ├── lib/
│   │   └── llama/                 # Prebuilt llama.cpp binaries & AVX/CUDA DLLs
│   │       ├── llama-server.exe   # llama.cpp server executable
│   │       ├── llama-cli.exe      # llama.cpp CLI test tool
│   │       └── ggml-*.dll         # Hardware-optimized CPU/CUDA backends
│   ├── services/                  # Execution modules and OS connectors
│   │   ├── app_opener.py          # Windows Start menu app indexing & launching
│   │   ├── calendar_integration.py# Local & online calendar event management
│   │   ├── content_generator.py   # Multi-format document writing and export
│   │   ├── get_media.ps1          # PowerShell hook for Windows media metadata
│   │   ├── mail_integration.py    # Email reading, searching, draft composing
│   │   ├── os_automation.py       # Windows volume, keyboard media keys, power
│   │   ├── os_settings.py         # Windows system settings toggles & catalog
│   │   ├── screen_pen.py          # On-screen drawing & annotation canvas
│   │   ├── screen_vision.py       # Screen capture & vision OCR analysis
│   │   ├── settings_catalog.json  # Manifest of Windows Settings URI & keys
│   │   ├── weather.py             # Open-Meteo weather report fetcher
│   │   └── web_search.py          # Live web search & website content scraping
│   ├── ui/                        # Backend UI servers and input bridges
│   │   ├── hotkey_service.py      # Global Windows hotkey listener (Alt+V)
│   │   └── server.py              # [FATHER FILE] Flask REST API & SSE Hub
│   └── utils/                     # Shared low-level utilities
│       ├── calculate.py           # Math parsing and safe calculation engine
│       ├── network_utils.py       # Connection monitoring & ping checks
│       ├── settings_resolver.py   # Resolves natural language to OS settings
│       ├── tool_registry.py       # Action dispatcher, schemas, and card builder
│       └── tts.py                 # Kokoro TTS (ONNX) + Vosk STT audio driver
├── assets/                        # Audio sound effects (chimes, clicks)
├── models/                        # Binary models (GGUF, ONNX, Vosk)
│   ├── LFM3-Omni-2B-*.gguf        # Primary local GGUF omni model
│   ├── kokoro/                    # Kokoro-v1.0 ONNX model + voice vectors
│   └── vosk/                      # Vosk acoustic models for offline STT
├── rag_data/                      # User RAG storage (file index, hashes, SQLite)
│   ├── app_index.json             # Indexed Windows applications cache
│   ├── file_hashes.json           # File hash tracking for incremental RAG
│   └── file_index.json            # Extracted document chunks and metadata
├── scripts/
│   └── amigo_main.py              # [CLI FATHER FILE] Headless terminal runtime
├── tests/                         # Pytest test suites
│   ├── test_amigo_capabilities.py # End-to-end tool calling verification
│   ├── test_calculate.py          # Unit tests for math solver
│   ├── test_content_generator.py  # Unit tests for document generation
│   ├── test_document_evidence.py  # Tests for RAG chunk grounding
│   ├── test_llm_agent_model_config.py # Tests for LLM model parameters
│   └── test_os_settings.py        # Tests for OS settings manipulation
├── ui_app/                        # Frontend Web Dashboard (React + Vite + TS)
│   ├── src/
│   │   ├── components/            # UI components (Chat, Voice, HUD, Cards)
│   │   │   ├── ActionCard.tsx     # Displays tool action executions in UI
│   │   │   ├── ChatWorkspace.tsx  # Central chat messaging and streaming
│   │   │   ├── IntentBridgeHUD.tsx# Visual HUD displaying current intent/tool
│   │   │   ├── VoiceControls.tsx  # Mic controls, orb visualizer, voice selector
│   │   │   ├── SettingsPage.tsx   # Model selection, voices, proactive toggles
│   │   │   └── TitleBar.tsx       # Windows custom title bar & window controls
│   │   ├── services/
│   │   │   └── assistantApi.ts    # REST API & SSE EventSource client
│   │   ├── App.tsx                # React app root & state orchestrator
│   │   └── main.tsx               # Vite entry point
│   ├── package.json               # Frontend dependencies (Lucide, Tailwind, etc.)
│   └── vite.config.ts             # Vite build & proxy configuration
├── AMIGO_ACTIONS_AND_CAPABILITIES.md # Complete capability matrix & prompt format
├── amigo_profile.json             # User identity, facts, and voice preferences
├── requirements.txt               # Python package dependencies
└── setup.py                       # Python package configuration
```

---

## 3. Deep-Dive: File-by-File Reference

### 3.1 `amigo/ui/server.py` — The Master Backend ("Father File")
- **Role:** Central nervous system of Amigo for the web interface and background services.
- **Port:** HTTP 5000 (Flask / Waitress WSGI).
- **Core Responsibilities:**
  1. **SSE Broadcaster (`EventBroadcaster`):** Maintains open EventSource streams to all connected React clients. Dispatches events: `assistant_state`, `speech_start`, `speech_stop`, `action_card`, `system_stats`, `proactive_alert`.
  2. **Query Pipeline (`_process_query`):** Takes text or voice transcript, extracts clipboard/screen context, retrieves RAG memory, invokes `llm_agent` to plan actions, runs tools via `tool_registry`, synthesizes Kokoro audio, and returns structured JSON responses.
  3. **REST Endpoints:** Over 35 endpoints covering file upload, audio transcription, voice control, system statistics, Windows settings, weather, media controls, email, calendar, and reminders.
  4. **Async Bootstrapper (`initialize_amigo_async`):** Launches background workers (RAG indexer, reminders, proactive loop, hotkey listener, LLM warmup) concurrently so the web server starts with sub-second latency.

### 3.2 `scripts/amigo_main.py` — The Master CLI ("Father File (CLI)")
- **Role:** Independent CLI entry point for headless or terminal-only operation.
- **Key Features:**
  - `BargeInMonitor`: Background audio listener that detects user voice while TTS is speaking and triggers instant audio interruption (`stop_speaking()`).
  - Terminal interactive loop supporting Voice mode (Vosk STT), Type mode, or Hybrid fallback mode.
  - Direct execution of `tool_registry.execute_tool` and streaming audio output.

### 3.3 `amigo/core/llm_agent.py` — The Brain & Reasoning Engine
- **Role:** Interfacing with local LLMs (via llama.cpp), prompt generation, JSON tool routing, and memory retention.
- **Key Functions:**
  - `init_local_llm()`: Spawns or connects to `llama-server.exe` with optimal thread count, context window (8192 tokens), and GPU layers.
  - `get_agent_actions(query, conversation_history)`: Synthesizes a system prompt with current date/time, OS context, active apps, and available tool signatures. Emits a structured JSON action plan.
  - `get_ai_response_stream(query)`: Yields streaming text responses for natural conversation.
  - `add_to_memory(user_msg, ai_reply)`: Stores interactions into long-term history.

### 3.4 `amigo/core/rag_engine.py` & `rag_indexer.py` — Semantic Memory
- **Role:** Indexing local files (PDFs, Markdown, text, code) and conversation transcripts.
- **Engine:** Combines lexical search (BM25) with semantic embeddings and re-ranking for ultra-precise factual recall without cloud dependencies.
- **Indexer:** Background thread that checks directory changes using MD5 hashes (`file_hashes.json`) and updates the search database (`file_index.json`).

### 3.5 `amigo/core/proactive_intelligence.py` — Autonomous Daemon
- **Role:** Runs periodic background evaluation loops to detect if the user needs assistance:
  - Upcoming calendar appointments or impending reminder triggers.
  - High CPU/RAM pressure detection.
  - Stale clipboard analysis (e.g., offering to summarize copied long text).
  - Emits proactive alerts over SSE to the React frontend.

### 3.6 `amigo/utils/tool_registry.py` — Unified Tool Hub
- **Role:** Registry and execution harness for every assistant skill.
- **Key Functions:**
  - `execute_tool(tool_name, params)`: Dispatches to the corresponding service module.
  - `build_action_cards(tool, params, result)`: Creates formatted visual cards (e.g. weather widgets, search previews, email drafts) displayed in the UI.

### 3.7 `amigo/utils/tts.py` — Speech Input & Output Driver
- **Text-to-Speech:** Runs `kokoro-v1.0.onnx` with local voice vectors (10 unique voices including Bella, Sarah, Nicole, Michael, Adam). Supports streaming sentence chunking.
- **Speech-to-Text:** Runs `vosk` offline acoustic model for zero-latency local speech transcription from microphone WAV buffers.

### 3.8 `amigo/services/*` — OS Automation Modules
- **`app_opener.py`:** Parses Windows Start Menu shortcuts (`.lnk`), builds an application database, and launches apps by fuzzy matching.
- **`os_automation.py`:** Direct Windows API controls for volume up/down/mute, play/pause media keys, next/prev track, and screen brightness.
- **`os_settings.py`:** Interacts with Windows Settings URIs (e.g., `ms-settings:network-wifi`, `ms-settings:bluetooth`) and PowerShell toggles.
- **`screen_vision.py`:** Captures full-resolution or windowed screenshots and feeds them into local vision models.
- **`web_search.py`:** Scrapes DuckDuckGo / SearXNG search results and extracts live page content for real-time web awareness.

---

## 4. End-to-End Query Execution Lifecycle

```
1. Input Received
   ├─ UI Chat: POST /api/assistant/process
   ├─ UI Voice: WebRTC Audio Blob -> POST /api/transcribe -> /api/assistant/process
   └─ CLI: take_command() -> Vosk STT -> process_agent_query()

2. Context Assembly
   ├─ RAG Search: rag_engine.get_relevant_context(query)
   ├─ Clipboard: amigo.core.llm_agent.get_clipboard_text()
   └─ Screen: optional base64 screenshot if vision requested

3. Action Routing
   ├─ Prompt sent to llama.cpp with tool definitions
   └─ LLM outputs JSON action plan: [{"tool": "...", "params": {...}}]

4. Tool Execution
   ├─ tool_registry.execute_actions(actions)
   └─ Target service executes (e.g. os_settings, web_search, weather)

5. Response & Presentation
   ├─ ActionCard synthesized and broadcasted via SSE (/api/events)
   ├─ Text reply synthesized into audio via Kokoro ONNX
   └─ Turn saved to RAG memory database
```

---

## 5. Coding & Development Guidelines

1. **Non-blocking Operations:** Any OS automation or network request in `server.py` must either be fast or run asynchronously to keep SSE streaming responsive.
2. **Offline First:** All primary capabilities (LLM, TTS, STT, RAG, App Opener, OS Settings) must function without an active internet connection. Cloud fallbacks are used strictly when web search or weather is explicitly required.
3. **Safety Guards:** Destructive OS actions (system shutdown, deleting reminders) must implement user confirmation flags (`confirmed=False`).

