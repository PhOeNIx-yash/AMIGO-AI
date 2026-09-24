# Amigo Voice Assistant 🎙️✨

A private, intelligent, and agentic personal voice assistant powered locally by the **MiniCPM 5 2B** GGUF model, **Laya System 1 Neural Router**, **Sherpa-ONNX** real-time neural speech recognition, and **Kokoro ONNX** 24kHz studio neural speech synthesis.

Amigo provides desktop automation, browser & window control, document intelligence (invoices, PDFs, spreadsheets), live interactive action widgets (countdown timers, stopwatches, weather telemetry), deep web browsing, app management, and an adaptive RAG-powered vector memory system — completely offline, with **zero API keys and zero cloud dependencies**.

---

## 🌟 Key Capabilities & Features

- **100% Offline Local AI** — Powered by `MiniCPM 5 2B` via `llama-cpp-python` with optional CUDA GPU acceleration for near-instant inference.
- **⚡ Laya System 1 Neural Router** — Sub-second, non-autoregressive intent routing via [Convai Innovations' Laya](https://github.com/convai-innovations/laya). A dedicated ModernBERT decision model that classifies every query in a single parallel forward pass (`<2s`), routing actions deterministically to the correct tool before MiniCPM ever runs. Zero regex conflicts, zero duplicate routing paths.
- **Dual-Engine Architecture (System 1 + System 2)**:
  - **System 1 (Laya)** — Instant action classification: opens apps, controls windows, browser tabs, desktop input, volume, screenshots, timers, email, calendar, and more.
  - **System 2 (MiniCPM 5 2B)** — Deep conversational reasoning, Q&A, explanations, math, jokes, and general chat. Only invoked when Laya confirms `intent == "chat"`.
- **Multi-Step Compound Command Chains** — Execute complex commands like *"Open Notepad and type Hello World"* or *"Minimize all windows and check weather in Tokyo"* as atomically decomposed sequential action chains, each step routing through Laya at sub-40ms transitions.
- **Desktop & Windows Automation** — Type text, press keys, click screen coordinates, manage windows (minimize all, maximize, switch), and launch or close any Windows application.
- **Browser & Web Page Control** — Open new tabs, close tabs, switch between tabs, scroll pages up/down, and visit URLs — all via voice.
- **Offline Speech-to-Text (STT)** — Powered by `Sherpa-ONNX` (`streaming Zipformer`) for ultra-low latency (<50ms) streaming voice command recognition, lightweight memory footprint (~80MB RAM), and zero CPU saturation.
- **24kHz Studio Neural TTS** — `Kokoro ONNX` delivers expressive, studio-grade speech synthesis with 10 curated voices (5 Female: *Nicole, Sarah, Heart, Sky, Bella*; 5 Male: *Adam, Michael, Echo, Liam, George*), low-latency audio streaming, and built-in phonetic text expansion for numbers, acronyms, scores, dates, and currency.
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

## 🧠 Dual-Engine Neural Architecture

Amigo uses a **two-model pipeline** for zero-conflict, low-latency task execution:

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
  │  Tier 2: Laya System 1 Neural Router (ALL Actions)  │
  │  laya_router.route_intent_via_laya()                │
  │  • Single parallel forward pass (<2s)               │
  │  • Priority fast-paths (<1ms): scroll, lock, tab,   │
  │    press, type, email, calendar, weather, timer     │
  │  • 100% action precision — no false positives       │
  └─────────────────────────────────────────────────────┘
        │ intent == "chat" OR conf < 0.35
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
├── ui_server.py            # Flask REST API, SSE event streaming & UI server
├── laya_router.py          # Laya System 1 Neural Router (sub-second intent routing)
├── task_agent.py           # Laya-powered multi-step task decomposer & chain executor
├── local_llm.py            # MiniCPM 5 2B GGUF engine & agentic pipeline controller
├── tts.py                  # Unified speech engine (Kokoro ONNX TTS & Sherpa-ONNX STT)
├── tool_registry.py        # Central tool registry, dispatcher & action cards
├── rag_engine.py           # ChromaDB vector store, semantic search & prompt injection
├── rag_indexer.py          # Background file crawling & incremental hash indexer
├── mail_integration.py     # Outlook email integration & RAG indexing
├── calendar_integration.py # Outlook calendar events & schedule RAG
├── reminder_timer.py       # Background timer & reminder scheduling engine
├── ai.py                   # RAG memory bridge & voice prompt constructor
├── weather.py              # Keyless dynamic weather telemetry
├── app_opener.py           # Dynamic Windows application resolver & launcher
├── os_automation.py        # System volume, brightness, screenshots & shortcuts
├── screen_vision.py        # Screen vision & Windows OCR fallback
├── Searchnow.py            # Web search & YouTube playback integration
├── Calculatenumbers.py     # Fast arithmetic & mathematical evaluator
├── settings_resolver.py    # Windows Settings ms-settings: URI resolver
├── amigo main.py           # Terminal CLI voice & keyboard interface
├── models/
│   ├── laya/               # Laya System 1 weights (model.safetensors + rl_agent_config.json)
│   └── minicpm/            # MiniCPM 5 2B GGUF weights
└── ui_app/                 # Modern React + Vite frontend source code
    ├── src/
    │   ├── components/     # ActionCard, CanvasVisualizer, KineticText, SettingsPage, etc.
    │   ├── services/       # assistantApi.ts (REST & fallback parsing)
    │   ├── utils/          # audio.ts, theme tokens, etc.
    │   └── types.ts        # TypeScript schemas
    └── dist/               # Compiled production bundle
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
1. Installs all Python dependencies via `pip install -r requirements.txt`.
2. Downloads the `Kokoro ONNX` neural voice models (`kokoro-v1.0.onnx` & `voices-v1.0.bin`).
3. Downloads the `MiniCPM 5 2B` quantized GGUF model (~1.56 GB).
4. Initializes RAG vector data storage directories and default profile.
5. Verifies or compiles the Web Dashboard production build (`ui_app/dist`).

> **Optional — Laya System 1 Router**: Place `model.safetensors` and `rl_agent_config.json` into `models/laya/`. When present, Amigo automatically activates the Laya System 1 Neural Router for sub-second action routing. Without it, the pipeline falls back gracefully to MiniCPM 5 2B for all queries.

### Option 2: Command Line Setup
```bash
# 1. Install requirements & download models
python setup.py

# 2. Launch the Web Dashboard
python ui_server.py
```

Open your browser at **`http://localhost:5000`** to access the Amigo Dashboard.

---

## 🎯 Running Amigo

### 1. Web Dashboard (Recommended)
```bash
python ui_server.py
# or double-click start_amigo.bat
```
- Full glowing visualizer with speech wave & particle orb morphing.
- Curated typography physics (Silk Emerge, Liquid Glide, Specular Sheen, Serene Float).
- Live countdown timer, stopwatch, and weather action cards.
- Dark & Light mode support with curated accent themes (Amigo Violet, Emerald, Amber, Cyan, Rose, Noir).
- Command history drawer and settings panel.

### 2. Terminal Voice / Keyboard Mode
```bash
python "amigo main.py"
```
Choose your preferred interaction mode:
- `[1]` Voice Only (Microphone speech recognition)
- `[2]` Keyboard Type Only (100% offline text input)
- `[3]` Voice + Keyboard fallback

---

## 🗣️ Supported Voice & Text Commands

| Category | Example Voice / Text Prompts |
|---|---|
| **Document Intelligence** | *"What is the PAN number from invoice?"*, *"What is the total amount in my electricity bill?"*, *"Summarize this PDF"* |
| **File Search** | *"Find documents related to GST"*, *"Find my tax reports"*, *"Open invoice.pdf"* |
| **App Launching** | *"Open Spotify"*, *"Open Calculator"*, *"Launch Visual Studio Code"*, *"Open Notepad"* |
| **App Closing** | *"Close Notepad"*, *"Quit Chrome"*, *"Exit VS Code"* |
| **Desktop Input** | *"Type Hello World into Notepad"*, *"Press Enter"*, *"Press Ctrl+S"*, *"Click at 500 300"* |
| **Window Management** | *"Minimize all windows"*, *"Maximize window"*, *"Switch window"* |
| **Browser Control** | *"Open new tab"*, *"Close tab"*, *"Next tab"*, *"Scroll down"*, *"Scroll up"* |
| **Timers & Stopwatch** | *"Set a timer for 1 minute"*, *"Set a timer for 25 minutes for focus"*, *"Start stopwatch"* |
| **Live Weather** | *"What's the weather today?"*, *"Weather in Tokyo"*, *"What's the temperature in Paris?"* |
| **Music & YouTube** | *"Play Interstellar soundtrack on YouTube"*, *"Play relaxing jazz"* |
| **System Controls** | *"Set volume to 50%"*, *"Mute volume"*, *"Set brightness to 80%"*, *"Take a screenshot"* |
| **OS Management** | *"Sleep PC"*, *"Lock computer"*, *"Lock my PC"*, *"Cancel shutdown"*, *"Restart PC"* |
| **Information & Web** | *"Search Google for quantum computing"*, *"Who was Alan Turing?"* |
| **Calculations** | *"What is 45 times 18?"*, *"Calculate the square root of 144"*, *"What is 50 * 2?"* |
| **Date & Time** | *"What time is it?"*, *"What is today's date?"* |
| **Memory & Profile** | *"Remember that my favorite color is teal"*, *"What is my name?"* |
| **Email & Calendar** | *"Check my unread emails"*, *"What is on my calendar today?"* |
| **Multi-Step Chains** | *"Open Notepad and type Hello World"*, *"Minimize all windows and check weather in Tokyo"* |

---

## 🔒 Privacy & Local Processing

Amigo is designed from the ground up for privacy:
- All LLM reasoning runs locally on your machine via `llama-cpp-python`.
- All action routing runs locally via the Laya System 1 Neural Router (no cloud inference).
- All speech recognition (STT) runs 100% locally on your machine via `sherpa-onnx`.
- All neural TTS speech generation runs locally on your machine via `kokoro-onnx`.
- Memory vector embeddings and user profiles remain entirely on your local filesystem.
- Zero analytics, zero data harvesting, zero tracking.
