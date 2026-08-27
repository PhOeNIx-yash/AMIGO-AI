# Amigo Voice Assistant 🎙️✨

A private, intelligent, and agentic personal voice assistant powered locally by the **Qwen 3.5 2B Instruct** GGUF model and **Kokoro ONNX** neural speech synthesis. 

Amigo provides desktop automation, live interactive action widgets (countdown timers, stopwatches, weather telemetry), deep web browsing, app management, and an adaptive RAG-powered vector memory system — completely offline, with **zero API keys and zero cloud dependencies**.

---

## 🌟 Key Capabilities & Features

- **100% Offline Local AI** — Powered by `Qwen 3.5 2B Instruct` via `llama-cpp-python` with optional CUDA GPU acceleration for near-instant inference.
- **Neural Text-to-Speech (TTS)** — `Kokoro ONNX` delivers expressive, human-like voice synthesis locally with asynchronous streaming, with automatic fallback to native Windows SAPI.
- **Fluid Web & Desktop Dashboard** — Built with React, TypeScript, Tailwind CSS, and Framer Motion, served via high-performance Flask SSE streaming at `http://localhost:5000`.
- **Interactive Action Cards & Widgets**:
  - **Live Countdown Timer & Stopwatch** — Interactive SVG circular progress ring, real-time countdown/countup, play/pause, reset, and `+1m`/`+5m` quick extension buttons with desktop notifications.
  - **Atmospheric Weather Telemetry** — Dynamic condition palettes, live temperatures (°C / °F toggle), humidity, wind velocity, UV index, feels-like temperature, and multi-period diurnal forecasts fetched dynamically (zero hardcoding).
  - **Multi-Action Confirmation Cards** — Clean interactive cards for multi-step tasks.
- **Intent Bridge HUD** — Streamlined single-pill intent router showing live agentic tool execution status (e.g. YouTube playback, application launching, system volume adjustments).
- **Audio Visualizer Engine**:
  - **Smooth Ribbon Waveform** during voice listening.
  - **3D Particle Orb** during cognitive processing and tool execution.
- **Desktop & OS Automation** — Native app launcher, system sleep/restart, window management, screen brightness, volume master control, screenshot capture, and clipboard intelligence.
- **RAG Vector Memory & Document Intelligence** — ChromaDB vector store with sentence-transformers embeddings for unlimited semantic conversation history, user profiling, and local document Q&A (PDF, Word, PowerPoint, Code, Text).
- **Background File Indexer** — Automatically crawls and indexes user documents in the background with hash-based incremental delta updates.
- **Outlook Mail & Calendar Integration** — Local read-only access to Outlook emails and upcoming calendar schedules.
- **Context-Aware Screen Vision** — Windows native OCR for reading and answering questions about active windows and on-screen content.

---

## 🧠 RAG & Adaptive Memory Architecture

Amigo features a modular vector-embedded long-term memory system powered by **ChromaDB** and **sentence-transformers**:

### 1. Vector Memory Collections
- **`conversations`** — Every conversation turn is embedded and semantically retrievable across sessions.
- **`user_facts`** — Dedicated collection storing facts learned about the user (*"I'm a software developer"*, *"my favorite artist is Hans Zimmer"*).
- **`documents`** — Locally indexed files (PDF, DOCX, TXT, MD, PPTX, Code) for contextual Q&A.
- **`emails` & `calendar`** — Local Outlook emails and schedule events.

### 2. User Profile & Settings
Lightweight profile and active state configurations are maintained in `amigo_profile.json` (user identity, preferences, interaction style metrics, and UI configurations).

### 3. Sliding Window Context
Maintains recent conversational turns in memory for natural multi-turn follow-ups and conversational context.

---

## 🛠️ Project Structure

```
amigo-main/
├── setup.py                # Automated one-click installer & model downloader
├── setup.bat               # Windows one-click batch setup script
├── start_amigo.bat         # Windows one-click dashboard launcher
├── requirements.txt        # Python backend dependencies
├── ui_server.py            # Flask REST API, SSE event streaming & UI server
├── tts.py                  # Neural TTS engine (Kokoro ONNX / SAPI fallback)
├── tool_registry.py        # Central tool registry, dispatcher & action cards
├── rag_engine.py           # ChromaDB vector store, semantic search & prompt injection
├── rag_indexer.py          # Background file crawling & incremental hash indexer
├── mail_integration.py     # Outlook email integration & RAG indexing
├── calendar_integration.py    # Outlook calendar events & schedule RAG
├── local_llm.py            # Qwen 3.5 2B GGUF engine & zero-latency dispatcher
├── reminder_timer.py       # Background timer & reminder scheduling engine
├── ai.py                   # RAG memory bridge & voice prompt constructor
├── weather.py              # Keyless dynamic weather telemetry
├── app_opener.py           # Dynamic Windows application resolver & launcher
├── os_automation.py        # System volume, brightness, screenshots & shortcuts
├── screen_vision.py        # Local Windows OCR & screen capture
├── Searchnow.py            # Web search & YouTube playback integration
├── Calculatenumbers.py     # Fast arithmetic & mathematical evaluator
├── settings_resolver.py    # Windows Settings ms-settings: URI resolver
├── amigo main.py           # Terminal CLI voice & keyboard interface
└── ui_app/                 # Modern React + Vite frontend source code
    ├── src/
    │   ├── components/     # ActionCard, CanvasVisualizer, IntentBridgeHUD, etc.
    │   ├── services/       # assistantApi.ts (REST & fallback parsing)
    │   ├── utils/          # audio.ts, theme tokens, etc.
    │   └── types.ts        # TypeScript schemas
    └── dist/               # Compiled production bundle
```

---

## ⚡ Quick Start & Installation

### Option 1: One-Click Windows Setup (Recommended)
Double-click **`setup.bat`** in the repository root.  
This automatically:
1. Installs all Python dependencies via `pip`.
2. Downloads the `Kokoro ONNX` neural voice models (`kokoro-v1.0.onnx` & `voices-v1.0.bin`).
3. Downloads the `Qwen 3.5 2B Instruct` quantized GGUF model (~1.45 GB).
4. Sets up RAG vector data storage directories.
5. Verifies the React Web Dashboard build.

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
- Live countdown timer, stopwatch, and weather action cards.
- Dark & Light mode support with curated accent themes (Amigo Indigo, Cyber Emerald, Solar Amber, Electric Cyan, Rose Quartz).
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
| **Timers & Stopwatch** | *"Set a timer for 1 minute"*, *"Set a timer for 25 minutes for focus"*, *"Start stopwatch"*, *"Timer 30 seconds"* |
| **Live Weather** | *"What's the weather today?"*, *"Weather in Tokyo"*, *"What's the temperature in Paris?"* |
| **Music & YouTube** | *"Play Interstellar soundtrack on YouTube"*, *"Play relaxing jazz"* |
| **App Launching** | *"Open Spotify"*, *"Open Calculator"*, *"Launch Visual Studio Code"*, *"Open Notepad"* |
| **System Controls** | *"Set volume to 50%"*, *"Mute volume"*, *"Set brightness to 80%"*, *"Take a screenshot"* |
| **OS Management** | *"Sleep PC"*, *"Lock computer"*, *"Cancel shutdown"* |
| **Information & Web** | *"Search DuckDuckGo for quantum computing"*, *"Who was Alan Turing on Wikipedia?"* |
| **Calculations** | *"What is 45 times 18?"*, *"Calculate the square root of 144"* |
| **Date & Time** | *"What time is it?"*, *"What is today's date?"* |
| **Memory & Facts** | *"Remember that my favorite color is teal"*, *"What is my name?"* |
| **Email & Calendar** | *"Check my unread emails"*, *"What is on my calendar today?"* |

---

## 🔒 Privacy & Local Processing

Amigo is designed from the ground up for privacy:
- All LLM reasoning runs locally on your machine via `llama-cpp-python`.
- All neural TTS speech generation runs locally on your CPU/GPU via `kokoro-onnx`.
- Memory vector embeddings and user profiles remain entirely on your local filesystem.
- Zero analytics, zero data harvesting, zero tracking.

