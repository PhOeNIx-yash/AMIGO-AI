# Amigo Voice Assistant 🎙️✨

A private, intelligent, and agentic personal voice assistant powered locally by the **Qwen 3.5 2B Instruct** GGUF model, **OpenAI Whisper** offline speech recognition, and **Kokoro ONNX** 24kHz studio neural speech synthesis. 

Amigo provides desktop automation, document intelligence (invoices, PDFs, spreadsheets), live interactive action widgets (countdown timers, stopwatches, weather telemetry), deep web browsing, app management, and an adaptive RAG-powered vector memory system — completely offline, with **zero API keys and zero cloud dependencies**.

---

## 🌟 Key Capabilities & Features

- **100% Offline Local AI** — Powered by `Qwen 3.5 2B Instruct` via `llama-cpp-python` with optional CUDA GPU acceleration for near-instant inference.
- **Offline Speech-to-Text (STT)** — Powered by `OpenAI Whisper` (`base.en`) for state-of-the-art voice command recognition, immune to background noise and fluent in technical jargon, gaming terms, and diverse accents.
- **24kHz Studio Neural TTS** — `Kokoro ONNX` delivers expressive, studio-grade speech synthesis with 10 curated voices (5 Female: *Nicole, Sarah, Heart, Sky, Bella*; 5 Male: *Adam, Michael, Echo, Liam, George*), low-latency audio streaming, and built-in phonetic text expansion for numbers, acronyms, scores, dates, and currency.
- **Curated High-End Kinetic Typography** — 4 luxury, fluid animation styles designed with Apple and Linear aesthetics:
  - **Silk Emerge (`silk_blur`)** — Apple-grade optical Gaussian blur dissipation & gentle vertical drift.
  - **Liquid Glide (`fluid_glide`)** — Organic, critically damped spring upward glide with zero cartoon bounce.
  - **Specular Sheen (`ambient_shimmer`)** — Refined metallic light sheen sweep across typography.
  - **Serene Float (`calm_breathe`)** — Subtle, low-amplitude anti-gravity breath for tranquil focus.
- **RAG Document Intelligence & Invoice Q&A** — Extract numbers, PANs, PINs, dates, amounts, and facts from local documents (`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.txt`, `.csv`, `.md`) without privacy lectures or refusals.
- **Autonomous Function Calling Router** — Pure LLM agentic tool dispatcher without hardcoded keyword regexes.
- **Deep Reasoning Toggle** — Real-time toggle to enable or disable step-by-step `<think>` internal deliberation on the fly.
- **Interactive Action Cards & Widgets**:
  - **Live Countdown Timer & Stopwatch** — Interactive SVG circular progress ring, real-time countdown/countup, play/pause, reset, and `+1m`/`+5m` quick extension buttons with desktop notifications.
  - **Atmospheric Weather Telemetry** — Dynamic condition palettes, live temperatures (°C / °F toggle), humidity, wind velocity, UV index, feels-like temperature, and multi-period diurnal forecasts fetched dynamically (zero hardcoding).
  - **Multi-Action Confirmation Cards** — Clean interactive cards for multi-step tasks.
- **Intent Bridge HUD** — Streamlined single-pill intent router showing live agentic tool execution status (e.g. YouTube playback, application launching, system volume adjustments).
- **Desktop & OS Automation** — Native app launcher, system sleep/restart, window management, screen brightness, volume master control, screenshot capture, and clipboard intelligence.
- **Global System-Wide Wake Hotkey (`Alt + V`)** — Wake Amigo from ANY active Windows application (VS Code, Word, Excel, games, or browser). Instantly captures the foreground screen context and listens for your voice command without ever needing the browser open.
- **Native Multimodal Screen & Image Vision** — Natively powered by Qwen 3.5 2B multimodal projector (`mmproj`) to comprehend desktop screens, graphs, UI layouts, and uploaded images directly without relying solely on OCR, with automatic fallback to Windows Media OCR (`winocr`).

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
├── tts.py                  # Unified speech engine (Kokoro ONNX TTS & OpenAI Whisper STT)
├── tool_registry.py        # Central tool registry, dispatcher & action cards
├── rag_engine.py           # ChromaDB vector store, semantic search & prompt injection
├── rag_indexer.py          # Background file crawling & incremental hash indexer
├── mail_integration.py     # Outlook email integration & RAG indexing
├── calendar_integration.py # Outlook calendar events & schedule RAG
├── local_llm.py            # Qwen 3.5 2B GGUF engine & agentic tool caller
├── reminder_timer.py       # Background timer & reminder scheduling engine
├── ai.py                   # RAG memory bridge & voice prompt constructor
├── weather.py              # Keyless dynamic weather telemetry
├── app_opener.py           # Dynamic Windows application resolver & launcher
├── os_automation.py        # System volume, brightness, screenshots & shortcuts
├── screen_vision.py        # Native multimodal screen vision & Windows OCR fallback
├── Searchnow.py            # Web search & YouTube playback integration
├── Calculatenumbers.py     # Fast arithmetic & mathematical evaluator
├── settings_resolver.py    # Windows Settings ms-settings: URI resolver
├── amigo main.py           # Terminal CLI voice & keyboard interface
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
3. Downloads the `Qwen 3.5 2B Instruct` quantized GGUF model (~1.45 GB).
4. Initializes RAG vector data storage directories and default profile.
5. Verifies or compiles the Web Dashboard production build (`ui_app/dist`).

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
| **Timers & Stopwatch** | *"Set a timer for 1 minute"*, *"Set a timer for 25 minutes for focus"*, *"Start stopwatch"* |
| **Live Weather** | *"What's the weather today?"*, *"Weather in Tokyo"*, *"What's the temperature in Paris?"* |
| **Music & YouTube** | *"Play Interstellar soundtrack on YouTube"*, *"Play relaxing jazz"* |
| **App Launching** | *"Open Spotify"*, *"Open Calculator"*, *"Launch Visual Studio Code"*, *"Open Notepad"* |
| **System Controls** | *"Set volume to 50%"*, *"Mute volume"*, *"Set brightness to 80%"*, *"Take a screenshot"* |
| **OS Management** | *"Sleep PC"*, *"Lock computer"*, *"Cancel shutdown"* |
| **Information & Web** | *"Search Google for quantum computing"*, *"Who was Alan Turing?"* |
| **Calculations** | *"What is 45 times 18?"*, *"Calculate the square root of 144"* |
| **Date & Time** | *"What time is it?"*, *"What is today's date?"* |
| **Memory & Profile** | *"Remember that my favorite color is teal"*, *"What is my name?"* |
| **Email & Calendar** | *"Check my unread emails"*, *"What is on my calendar today?"* |

---

## 🔒 Privacy & Local Processing

Amigo is designed from the ground up for privacy:
- All LLM reasoning runs locally on your machine via `llama-cpp-python`.
- All speech recognition (STT) runs 100% locally on your machine via `openai-whisper`.
- All neural TTS speech generation runs locally on your machine via `kokoro-onnx`.
- Memory vector embeddings and user profiles remain entirely on your local filesystem.
- Zero analytics, zero data harvesting, zero tracking.

