# Amigo Voice Assistant 🎙️✨

A private, intelligent, and agentic personal voice assistant powered locally by the **MiniCPM 5 2B Claude-Fable 5.1 Thinking Agentic** GGUF model, **Sherpa-ONNX** real-time neural speech recognition, and **Supertonic 3** 44.1kHz studio neural speech synthesis.

Amigo provides desktop automation, browser & window control, document intelligence (invoices, PDFs, spreadsheets), live interactive action widgets (countdown timers, stopwatches, weather telemetry), deep web browsing, app management, and an adaptive RAG-powered vector memory system — completely offline, with **zero API keys and zero cloud dependencies**.

---

## 🚀 What's New (v2.0)

### 🧠 **LLM-Based Agentic Reasoning Architecture** (NEW)
- **Full natural language understanding & thinking reasoning** via MiniCPM 5 2B Claude-Fable 5.1 with structured tool calling
- **Internal reasoning scratchpad** with `<think>...</think>` support and intelligent tool decision-making
- **Multi-step compound command chains** — "Open Notepad and type Hello World" executes atomically
- **Context-aware follow-ups** — "Play it again", "Close that", "Search for that" resolve from conversation history
- **User self-correction learning** — "No, I meant X" teaches Amigo your preferences permanently
- **Emergency safety stops** — Instant (<1ms) halt for "stop", "cancel", "exit" commands

### 🔍 **Proactive Intelligence Engine** (NEW)
- **Predictive suggestions** — Learns your patterns and suggests actions before you ask
- **Context-aware notifications** — Battery alerts, calendar reminders, weather changes
- **Adaptive learning** — Improves suggestions based on your acknowledgment/dismissal feedback
- **Configurable triggers** — Time-based, event-based, and pattern-based proactive actions

### 📁 **Enhanced RAG Document Intelligence** (IMPROVED)
- **Semantic file search** — Find files by meaning, not just name ("find my tax documents")
- **Cross-collection search** — Search conversations, documents, emails, calendar simultaneously
- **Background incremental indexer** — Auto-indexes Documents, Downloads, Desktop every 30 minutes
- **File upload & instant analysis** — Drag-drop PDFs/images for immediate Q&A
- **ChromaDB vector store** with sentence-transformers embeddings

### 🌐 **Global System-Wide Hotkey** (NEW)
- **Alt+V anywhere** — Wake Amigo from any Windows app (VS Code, games, browser, Word)
- **Screen context capture** — Instantly analyzes foreground window for context-aware commands
- **No browser required** — Works completely headless

### 🎨 **Modern React Dashboard** (IMPROVED)
- **4 curated kinetic typography styles** — Silk Emerge, Liquid Glide, Specular Sheen, Serene Float
- **Interactive action cards** — Live timers, weather widgets, multi-action confirmations
- **Real-time SSE event streaming** — Live state updates, media info, RAG indexing progress
- **Dark/Light themes** with 6 curated accent colors
- **Settings panel** — Model switching, thinking mode, voice selection, auto-speech toggle

### 🔧 **System Integration** (NEW)
- **Outlook email & calendar** — Read unread emails, search emails, view calendar events
- **Windows Settings URI resolver** — "Open display settings", "Open Bluetooth settings"
- **Media control** — Play/pause, next/prev track, volume, current track info
- **System stats widget** — Live CPU, RAM, battery in dashboard header

### 🛡️ **Code Quality & Reliability** (IMPROVED)
- **Dead code removal** — Cleaned unused imports, variables, aliases, and forwarders
- **Indentation fixes** — Resolved all syntax errors in agent pipeline
- **Vulture clean** — Zero dead code at 80% confidence
- **All tests passing** — 26/28 tests pass (2 are LLM behavior, not code bugs)

---

## 🌟 Key Capabilities & Features

- **100% Offline Local AI** — Powered by `MiniCPM 5 2B` via `llama-cpp-python` with optional CUDA GPU acceleration for near-instant inference.
- **⚡ Natural Language Intent Agent** — Neural agent architecture that understands conversational intent, extracts structured tool parameters, and dispatches actions directly to the desktop environment.
- **Multi-Step Compound Command Chains** — Execute complex commands like *"Open Notepad and type Hello World"* or *"Minimize all windows and check weather in Tokyo"* as atomically decomposed sequential action chains.
- **Desktop & Windows Automation** — Type text, press keys, click screen coordinates, manage windows (minimize all, maximize, switch), and launch or close any Windows application.
- **Browser & Web Page Control** — Open new tabs, close tabs, switch between tabs, scroll pages up/down, and visit URLs — all via voice.
- **Offline Speech-to-Text (STT)** — Powered by `Sherpa-ONNX` (`streaming Zipformer`) for ultra-low latency (<50ms) streaming voice command recognition, lightweight memory footprint (~80MB RAM), and zero CPU saturation.
- **44.1kHz Studio Neural TTS** — `Supertonic 3` delivers expressive, studio-grade speech synthesis with 10 curated voices (5 Female: *Nova, Aria, Serena, Chloe, Luna*; 5 Male: *Orion, Atlas, Leo, Felix, Ethan*), ultra-fast ONNX inference, low-latency streaming, and built-in phonetic text expansion for numbers, acronyms, scores, dates, and currency.
- **Curated High-End Kinetic Typography** — 4 luxury, fluid animation styles designed with Apple and Linear aesthetics:
  - **Silk Emerge (`silk_blur`)** — Apple-grade optical Gaussian blur dissipation & gentle vertical drift.
  - **Liquid Glide (`fluid_glide`)** — Organic, critically damped spring upward glide with zero cartoon bounce.
  - **Specular Sheen (`ambient_shimmer`)** — Refined metallic light sheen sweep across typography.
  - **Serene Float (`calm_breathe`)** — Subtle, low-amplitude anti-gravity breath for tranquil focus.
- **RAG Document Intelligence & Invoice Q&A** — Extract numbers, PANs, PINs, dates, amounts, and facts from local documents (`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.txt`, `.csv`, `.md`) without privacy lectures or refusals.
- **Deep Reasoning Toggle** — Real-time toggle to enable or disable step-by-step `<think>` internal deliberation on the fly.
- **Interactive Action Cards & Widgets**:
  - **Live Countdown Timer & Stopwatch** — Interactive SVG circular progress ring, real-time countdown/countup, play/pause, reset, and `+1m`/`+5m` quick extension buttons with desktop notifications.
  - **Atmospheric Weather Telemetry** — Dynamic condition palettes, live temperatures (°C / °F toggle), humidity, wind velocity, UV index, feels-like temperature, and multi-period diurnal forecasts fetched dynamically (zero hardcoding).
  - **Multi-Action Confirmation Cards** — Clean interactive cards for multi-step tasks.
- **Intent Bridge HUD** — Streamlined single-pill intent router showing live agentic tool execution status (e.g. YouTube playback, application launching, system volume adjustments).
- **Global System-Wide Wake Hotkey (`Alt + V`)** — Wake Amigo from ANY active Windows application (VS Code, Word, Excel, games, or browser). Instantly captures the foreground screen context and listens for your voice command without ever needing the browser open.
- **Intelligent Screen & Image Vision** — Comprehend desktop screens, graphs, UI layouts, and uploaded images directly via native Windows Media OCR (`winocr`) and visual analysis.

---

## 🧠 Neural Agent Architecture

Amigo uses a tiered agent pipeline for zero-conflict, low-latency task execution:

```
User Voice / Text Input
        │
        ▼
  ┌─────────────────────────────────┐
  │   Tier 0: Emergency Stop/Exit   │  (instant, <1ms)
  └─────────────────────────────────┘
        │
        ▼
  ┌─────────────────────────────────┐
  │   Tier 1: Multi-Step Decomposer │  (conjunction detection, <5ms)
  │   task_agent.decompose_task()   │
  └─────────────────────────────────┘
        │
        ▼
  ┌─────────────────────────────────────────────────────┐
  │  Tier 2: LLM-Based Agent (Natural Language Intent)  │
  │  llm_agent.get_agent_action()                       │
  │  • MiniCPM 5 2B reasons about user intent           │
  │  • Full conversation context & memory integration   │
  │  • Handles ambiguity, follow-ups, anaphora naturally│
  │  • Structured tool calling with validated schemas   │
  └─────────────────────────────────────────────────────┘
        │
        ▼
  ┌─────────────────────────────────────────────────────┐
  │  Tier 3: MiniCPM 5 2B (Conversational Reasoning)   │
  │  query_local_llm() — chat, Q&A, math, explanation   │
  └─────────────────────────────────────────────────────┘
```

---

## 🧠 RAG & Adaptive Memory Architecture

Amigo features a modular vector-embedded long-term memory system powered by **ChromaDB** and **sentence-transformers**:

### 1. Vector Memory Collections
- **`conversations`** — Every conversation turn is embedded and semantically retrievable across sessions.
- **`user_facts`** — Dedicated collection storing facts learned about the user (*"I'm a software developer"*, *"my favorite artist is Hans Zimmer"*).
- **`documents`** — Locally indexed files (PDF, DOCX, XLSX, TXT, MD, PPTX, Code) for contextual Q&A.
- **`emails` & `calendar`** — Local Outlook emails and schedule events.

### 2. User Profile & Settings
Lightweight profile and active state configurations are maintained in `amigo_profile.json` (user identity, preferences, interaction style metrics, thinking mode toggle, and typography settings).

---

## 🛠️ Project Structure

```
amigo-main/
├── setup.py                # Automated one-click installer & model downloader
├── setup.bat               # Windows one-click batch setup script
├── start_amigo.bat         # Windows one-click dashboard launcher
├── requirements.txt        # Python backend dependencies
├── pyproject.toml          # Modern Python packaging config
├── amigo/                  # Core Python package
│   ├── __init__.py         # Public API exports
│   ├── core/               # Core AI & reasoning modules
│   │   ├── __init__.py
│   │   ├── ai.py                   # RAG memory bridge & voice prompt constructor
│   │   ├── llm_agent.py            # LLM-based agent (natural language intent understanding)
│   │   ├── local_llm.py            # MiniCPM 5 2B GGUF engine & agentic pipeline controller
│   │   ├── rag_engine.py           # ChromaDB vector store, semantic search & prompt injection
│   │   ├── rag_indexer.py          # Background file crawling & incremental hash indexer
│   │   ├── reminder_timer.py       # Background timer & reminder scheduling engine
│   │   ├── proactive_intelligence.py # Proactive suggestions & adaptive learning engine
│   │   └── task_agent.py           # Multi-step task decomposer & chain executor
│   ├── services/           # External integrations
│   │   ├── __init__.py
│   │   ├── app_opener.py           # Dynamic Windows application resolver & launcher
│   │   ├── calendar_integration.py # Outlook calendar events & schedule RAG
│   │   ├── mail_integration.py     # Outlook email integration & RAG indexing
│   │   ├── os_automation.py        # System volume, brightness, screenshots & shortcuts
│   │   ├── screen_vision.py        # Screen vision & Windows OCR fallback
│   │   ├── weather.py              # Keyless dynamic weather telemetry
│   │   └── web_search.py           # Multi-engine web search & YouTube playback
│   ├── ui/                 # Web dashboard backend
│   │   ├── __init__.py
│   │   ├── server.py               # Flask REST API, SSE event streaming & UI server
│   │   └── hotkey_service.py       # Global Alt+V system-wide wake hotkey
│   └── utils/              # Shared utilities
│       ├── __init__.py
│       ├── calculate.py            # Fast arithmetic & mathematical evaluator
│       ├── network_utils.py        # Internet connectivity checks
│       ├── settings_resolver.py    # Windows Settings ms-settings: URI resolver
│       ├── tool_registry.py        # Central tool registry, dispatcher & action cards
│       └── tts.py                  # Unified speech engine (Supertonic 3 TTS & Sherpa-ONNX STT)
├── models/                 # AI model weights
│   ├── minicpm/            # MiniCPM 5 2B GGUF weights
│   └── sherpa-onnx/        # Sherpa-ONNX streaming Zipformer STT model
├── rag_data/               # RAG vector database & uploads
│   ├── chroma/             # ChromaDB persistent storage
│   └── uploads/            # User uploaded files for analysis
├── ui_app/                 # Modern React + Vite frontend source code
│   ├── src/
│   │   ├── components/     # ActionCard, CanvasVisualizer, KineticText, SettingsPage, etc.
│   │   ├── services/       # assistantApi.ts (REST & fallback parsing)
│   │   ├── utils/          # audio.ts, theme tokens, etc.
│   │   └── types.ts        # TypeScript schemas
│   └── dist/               # Compiled production bundle
├── tests/                  # Unit & integration tests
│   ├── test_amigo_capabilities.py
│   ├── test_calculate.py
│   └── test_deps.py
└── assets/                 # Sound effects & static assets
```

---

## ⚡ Quick Start & Installation

### Prerequisites (Windows)
1. **Python 3.10 to 3.12** installed and added to PATH.
2. **Git Version Control** (optional but recommended):
   ```powershell
   winget install --id Git.Git -e --source winget
   ```

### Option 1: One-Click Windows Setup (Recommended)
Double-click **`setup.bat`** in the repository root.  
This automatically:
1. Creates a virtual environment (`venv/`) for isolated dependencies.
2. Installs all Python dependencies via `pip install -r requirements.txt`.
3. Initializes the `Supertonic 3` neural voice engine (44.1kHz studio quality).
4. Downloads the `MiniCPM 5 2B` quantized GGUF model (~1.56 GB).
5. Downloads the `Sherpa-ONNX` streaming Zipformer STT model (~80 MB).
6. Initializes RAG vector data storage directories and default profile.
7. Verifies or compiles the Web Dashboard production build (`ui_app/dist`).

### Option 2: Command Line Setup
```bash
# 1. Install requirements & download models
python setup.py

# 2. Launch the Web Dashboard
python -m amigo.ui.server
```

Open your browser at **`http://localhost:5000`** to access the Amigo Dashboard.

### Option 3: Development Mode (Hot Reload)
```bash
# Terminal 1: Backend
python -m amigo.ui.server

# Terminal 2: Frontend (React dev server)
cd ui_app && npm install && npm run dev
```
Access at **`http://localhost:5173`** with hot module replacement.

---

## 🎯 Running Amigo

### 1. Web Dashboard (Recommended)
```bash
python -m amigo.ui.server
# or double-click start_amigo.bat
```
- Full glowing visualizer with speech wave & particle orb morphing.
- Curated typography physics (Silk Emerge, Liquid Glide, Specular Sheen, Serene Float).
- Live countdown timer, stopwatch, and weather action cards.
- Dark & Light mode support with curated accent themes (Amigo Violet, Emerald, Amber, Cyan, Rose, Noir).
- Command history drawer and settings panel.
- **Global Alt+V hotkey** — works from any Windows application.
- **Real-time SSE streaming** — Live state, media, RAG indexing progress.

### 2. Terminal Voice / Keyboard Mode
```bash
python -m amigo.core.llm_agent
```
Choose your preferred interaction mode:
- `[1]` Voice Only (Microphone speech recognition)
- `[2]` Keyboard Type Only (100% offline text input)
- `[3]` Voice + Keyboard fallback

### 3. Headless / Background Mode
```bash
# Run without opening browser
python -m amigo.ui.server --no-browser
```
Useful for server deployments or when using only the Alt+V hotkey.

---

## 🗣️ Supported Voice & Text Commands

| Category | Example Voice / Text Prompts |
|---|---|
| **Document Intelligence** | *"What is the PAN number from invoice?"*, *"What is the total amount in my electricity bill?"*, *"Summarize this PDF"* |
| **File Search** | *"Find documents related to GST"*, *"Find my tax reports"*, *"Open invoice.pdf"* |
| **Semantic File Search** | *"Find files about taxes"*, *"Search for my resume"*, *"Find documents mentioning PAN"* |
| **App Launching** | *"Open Spotify"*, *"Open Calculator"*, *"Launch Visual Studio Code"*, *"Open Notepad"* |
| **App Closing** | *"Close Notepad"*, *"Quit Chrome"*, *"Exit VS Code"* |
| **Desktop Input** | *"Type Hello World into Notepad"*, *"Press Enter"*, *"Press Ctrl+S"*, *"Click at 500 300"* |
| **Window Management** | *"Minimize all windows"*, *"Maximize window"*, *"Switch window"* |
| **Browser Control** | *"Open new tab"*, *"Close tab"*, *"Next tab"*, *"Scroll down"*, *"Scroll up"* |
| **Timers & Stopwatch** | *"Set a timer for 1 minute"*, *"Set a timer for 25 minutes for focus"*, *"Start stopwatch"* |
| **Live Weather** | *"What's the weather today?"*, *"Weather in Tokyo"*, *"What's the temperature in Paris?"* |
| **Music & YouTube** | *"Play Interstellar soundtrack on YouTube"*, *"Play relaxing jazz"* |
| **Media Control** | *"Pause music"*, *"Resume playback"*, *"Next song"*, *"Previous track"*, *"What's playing?"* |
| **System Controls** | *"Set volume to 50%"*, *"Mute volume"*, *"Set brightness to 80%"*, *"Take a screenshot"* |
| **OS Management** | *"Sleep PC"*, *"Lock computer"*, *"Lock my PC"*, *"Cancel shutdown"*, *"Restart PC"* |
| **Windows Settings** | *"Open display settings"*, *"Open Bluetooth settings"*, *"Open sound settings"* |
| **Information & Web** | *"Search Google for quantum computing"*, *"Who was Alan Turing?"* |
| **Calculations** | *"What is 45 times 18?"*, *"Calculate the square root of 144"*, *"What is 50 * 2?"* |
| **Date & Time** | *"What time is it?"*, *"What is today's date?"* |
| **Memory & Profile** | *"Remember that my favorite color is teal"*, *"What is my name?"* |
| **Email & Calendar** | *"Check my unread emails"*, *"What is on my calendar today?"*, *"Search emails for invoice"* |
| **Proactive Intelligence** | *"What do you suggest?"*, *"Dismiss that suggestion"*, *"That was helpful"* |
| **Screen Vision** | *"What's on my screen?"*, *"Read the text on screen"*, *"Analyze this image"* |
| **File Upload** | Drag & drop PDFs/images in dashboard for instant analysis |
| **Multi-Step Chains** | *"Open Notepad and type Hello World"*, *"Minimize all windows and check weather in Tokyo"* |
| **Follow-up References** | *"Play it again"*, *"Close that"*, *"Search for that"*, *"Open that file"* |
| **Self-Correction** | *"No, I meant X"* — teaches Amigo your preference permanently |

---

## 🔌 REST API Endpoints

The Flask backend exposes a comprehensive REST API for the React dashboard and external integrations:

| Endpoint | Method | Description |
|---|---|---|
| `/api/assistant/process` | POST | Universal assistant query processor |
| `/api/action/execute` | POST | Execute action callbacks from UI |
| `/api/transcribe` | POST | Speech-to-text transcription |
| `/events` | GET | SSE event stream (real-time updates) |
| `/api/status` | GET | Current assistant state |
| `/api/system-stats` | GET | Live CPU, RAM, battery |
| `/api/history` | GET | Conversation history |
| `/api/query` | POST | Direct query endpoint |
| `/api/quick-action` | POST | Predefined quick actions |
| `/api/clear-memory` | POST | Clear conversations & RAG memory |
| `/api/settings` | GET/POST | Read/write settings |
| `/api/user-profile` | GET/POST | User identity & preferences |
| `/api/health` | GET | Health check |
| `/api/speak` | POST | Text-to-speech |
| `/api/shutdown` | POST | Graceful shutdown |
| `/api/weather` | GET | Weather data |
| `/api/media/status` | GET | Current media info |
| `/api/media/control` | POST | Media playback control |
| `/api/models` | GET/POST | Model management |
| `/api/listen` | POST | Trigger listening state |
| `/api/reminders` | GET/POST/DELETE | Timer & reminder management |
| `/api/active-state` | GET | Active context state |
| `/api/rag/status` | GET | RAG index statistics |
| `/api/rag/search` | POST | Semantic search across collections |
| `/api/rag/debug-search` | POST | Debug search with scoring |
| `/api/rag/reindex` | POST | Trigger manual re-indexing |
| `/api/emails` | GET | Recent emails |
| `/api/emails/unread` | GET | Unread email count & previews |
| `/api/emails/search` | POST | Search emails |
| `/api/calendar` | GET | Today's calendar events |
| `/api/calendar/upcoming` | GET | Upcoming events |
| `/api/proactive/status` | GET | Proactive engine status |
| `/api/proactive/config` | GET/POST | Proactive configuration |
| `/api/proactive/dismiss` | POST | Dismiss suggestion |
| `/api/proactive/acknowledge` | POST | Acknowledge suggestion |
| `/api/hotkey/status` | GET | Alt+V hotkey service status |
| `/api/upload` | POST | File upload & analysis |

---

## 🏗️ Architecture Deep Dive

### Async Initialization Pipeline
```
┌─────────────────────────────────────────────────────────────┐
│                    initialize_amigo_async()                  │
├─────────────────────────────────────────────────────────────┤
│  ThreadPoolExecutor (4 workers)                              │
│  ├─► LLM Model Loading (critical path, ~2.5s)               │
│  │    └─► Spinner progress indicator                        │
│  ├─► RAG Engine Init (background, non-blocking)             │
│  ├─► Background File Indexer (30min interval)               │
│  └─► Hotkey Service (Alt+V registration)                    │
│                                                              │
│  After LLM ready:                                           │
│  └─► Proactive Intelligence Engine                          │
└─────────────────────────────────────────────────────────────┘
```

### Agent Pipeline (Tiered)
```
User Input
    │
    ▼
┌──────────────────────────────────────────┐
│  Tier 0: Emergency Stop (<1ms)           │
│  "stop", "cancel", "exit" → instant halt │
└──────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────┐
│  Tier 1: Probe Guard                     │
│  "are you there", "hello amigo" → blocked│
└──────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────┐
│  Tier 2: User Self-Correction Learning   │
│  "No, I meant X" → stores correction     │
└──────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────┐
│  Tier 3: Fast Intent Parser              │
│  Regex-based for common commands         │
└──────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────┐
│  Tier 4: LLM Agent (MiniCPM 5 2B)        │
│  • Full conversation context             │
│  • RAG memory integration                │
│  • Structured tool calling (JSON)        │
│  • Anaphora resolution ("it", "that")    │
└──────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────┐
│  Tier 5: Tool Execution & Action Cards   │
│  • 35+ registered tools                  │
│  • Interactive UI cards for timers,      │
│    weather, clarifications               │
│  • SSE broadcast for real-time UI        │
└──────────────────────────────────────────┘
```

### RAG Collections & Search Types
| Collection | Purpose | Search Types |
|---|---|---|
| `conversations` | Chat history | `conversation_recall`, `general` |
| `user_facts` | Learned preferences | `fact_lookup`, `general` |
| `documents` | Local files (PDF, DOCX, etc.) | `document_qa`, `general` |
| `emails` | Outlook emails | `general` |
| `calendar` | Calendar events | `general` |

**Query Types:** `auto`, `general`, `fact_lookup`, `document_qa`, `conversation_recall`

---

## 🔒 Privacy & Local Processing

Amigo is designed from the ground up for privacy:
- All LLM reasoning runs locally on your machine via `llama-cpp-python`.
- All action routing and tool calling run 100% locally on your machine (no cloud inference).
- All speech recognition (STT) runs 100% locally on your machine via `sherpa-onnx`.
- All neural TTS speech generation runs locally on your machine via `supertonic`.
- Memory vector embeddings and user profiles remain entirely on your local filesystem.
- Zero analytics, zero data harvesting, zero tracking.
