# Amigo Voice Assistant: Complete Action & Capability Reference 🎙️⚡

This document provides a comprehensive, transparent inventory of everything **Amigo** can do, its system architecture, real command syntax, input parameters, and current operational health.

---

## 📊 Honest Status Summary

| Area | Rating | Status Details |
|---|:---:|---|
| **Local AI Models** | 🟢 **Healthy** | `MiniCPM 5 2B` (1.56 GB GGUF), `Kokoro ONNX` (24kHz TTS), and `Sherpa-ONNX` (Zipformer STT) are downloaded, present, and functional offline. |
| **Dependencies** | 🟢 **Healthy** | Core packages (`chromadb`, `sentence-transformers`, `CrossEncoder`, `rank_bm25`, `pdfplumber`, `winocr`, `psutil`) are installed and functional on Python 3.12. |
| **Web Dashboard UI** | 🟢 **Healthy** | React + Vite frontend in `ui_app/dist` is compiled, including live audio visualizers, countdown timers, stopwatches, weather widgets, and settings. |
| **OS Automation** | 🟢 **Healthy** | Audio volume, screen capture, app launching, brightness, keyboard shortcuts, window minimizing, and ms-settings URIs are fully operational. |
| **Intent Routing** | 🟢 **Healthy** | Native `llm_agent.py` using MiniCPM 5 2B for function calling and structured tool dispatching (Laya removed). |
| **Test Suite** | 🟢 **Cleaned** | Obsolete `laya_router` tests and invalid dependencies have been cleanly removed. |
| **Email & Calendar** | 🟡 **Conditional** | Works via local Microsoft Outlook COM (`win32com`). Requires classic Outlook desktop installed; does not sync with Web Outlook/Gmail without Outlook running. |

---

## 🛠️ Complete Tools & Actions Catalog

Amigo exposes **38 distinct agent tools** partitioned across 10 functional domains.

### 1. General Conversation & Knowledge
| Tool Name | Parameters | Spoken/Voice Prompt Examples | What It Does |
|---|---|---|---|
| `chat` | `response` (str) | *"Tell me a joke"*, *"Explain quantum computing"*, *"How are you?"* | Generates conversational, reasoning, or explanatory responses via MiniCPM 5 2B. Supports deep reasoning mode. |
| `calculate` | `expression` (str) | *"What is 45 times 18?"*, *"Calculate square root of 144"*, *"50 * 2"* | Evaluates arithmetic and mathematical expressions instantly using `Calculatenumbers.py`. |
| `get_time` | None | *"What time is it?"*, *"Tell me the current time"* | Returns local system time formatted naturally. |
| `get_date` | None | *"What's today's date?"*, *"What day of the week is it?"* | Returns current day, date, month, and year. |

---

### 2. Desktop & Windows Application Control
| Tool Name | Parameters | Spoken/Voice Prompt Examples | What It Does |
|---|---|---|---|
| `open_app` | `name` (str) | *"Open Notepad"*, *"Launch Spotify"*, *"Open VS Code"*, *"Start Calculator"* | Dynamically scans Windows Start Menu, Registry, Program Files, and AppData to launch applications. |
| `close_app` | `name` (str) | *"Close Notepad"*, *"Quit Chrome"*, *"Exit Spotify"* | Gracefully terminates matching running processes using `psutil`. |
| `open_folder` | `name` (str) | *"Open Downloads folder"*, *"Open Documents"*, *"Open Desktop"* | Opens Windows Explorer to standard user folders or custom directories. |
| `open_settings` | `setting` (str) | *"Open Bluetooth settings"*, *"Open Display settings"*, *"Open WiFi settings"* | Opens specific Windows 10/11 Settings pages directly via `ms-settings:` deep-links. |

---

### 3. File Discovery & Document Intelligence (RAG)
| Tool Name | Parameters | Spoken/Voice Prompt Examples | What It Does |
|---|---|---|---|
| `find_file` / `find_document` | `query` (str) | *"Find file budget.xlsx"*, *"Find tax invoices"*, *"Locate project report"* | Scans indexed files on the PC matching keywords or semantic meaning. Generates UI file selector cards. |
| `open_file` | `path` (str) | *"Open number 1"*, *"Open the invoice"* | Opens the selected file in its default Windows application. |
| `reveal_file` | `path` (str) | *"Reveal report.pdf in folder"*, *"Show file location"* | Opens Windows Explorer with the specific file highlighted. |
| `copy_file_path` | `path` (str) | *"Copy file path"* | Copies the absolute file path to the Windows clipboard. |
| `document_qa` / `ask_document` | `query` (str), `filepath` (opt) | *"What is the PAN number in the invoice?"*, *"What is the total amount in my electricity bill?"* | RAG vector search across indexed local files (`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.txt`, `.md`). |
| `summarize_document` | `filepath` (opt) | *"Summarize this PDF"*, *"Give me a summary of report.docx"* | Extracts document text and generates an executive summary. |
| `search_knowledge` | `query` (str) | *"Search my knowledge base for project roadmap"* | Searches across all indexed collections (docs, memory, emails, calendar). |

---

### 4. Screen Vision & OCR
| Tool Name | Parameters | Spoken/Voice Prompt Examples | What It Does |
|---|---|---|---|
| `take_screenshot` | None | *"Take a screenshot"*, *"Capture the screen"* | Captures full desktop screen and saves it as `amigo_screenshot.png`. |
| `screen_vision` / `read_screen` | `question` (opt) | *"What's on my screen?"*, *"Read the error message on my screen"*, *"What code is open?"* | Captures foreground window, runs native Windows Media OCR / pytesseract, and uses MiniCPM to answer questions about on-screen content. |
| `ask_about_screen` | `question` (str) | *"Explain the graph on my screen"*, *"What does this dialog say?"* | Targeted visual question-answering on active screen content. |

---

### 5. Media & Audio Control
| Tool Name | Parameters | Spoken/Voice Prompt Examples | What It Does |
|---|---|---|---|
| `play_youtube` | `query` (str) | *"Play Believer by Imagine Dragons"*, *"Play relaxing lofi music on YouTube"* | Resolves video stream or opens YouTube playback in browser. |
| `play_media` | None | *"Resume playback"*, *"Resume music"*, *"Play music"* | Sends Windows multimedia Play/Pause virtual key. |
| `pause_media` | None | *"Pause playback"*, *"Pause music"*, *"Stop music"* | Sends Windows multimedia Pause virtual key. |
| `next_track` | None | *"Next song"*, *"Skip track"*, *"Next video"* | Sends Windows multimedia Next Track key. |
| `prev_track` | None | *"Previous song"*, *"Previous track"*, *"Go back a song"* | Sends Windows multimedia Previous Track key. |
| `current_media` | None | *"What song is playing?"*, *"What's currently playing?"* | Inspects Windows System Media Transport Controls (SMTC) for current track metadata. |
| `set_volume` | `action` (str), `level` (int opt) | *"Set volume to 50%"*, *"Mute volume"*, *"Turn up the volume"*, *"Turn down volume"* | Adjusts master system audio level (0-100) or toggles mute. |

---

### 6. Interactive Timers, Stopwatches & Reminders
| Tool Name | Parameters | Spoken/Voice Prompt Examples | What It Does |
|---|---|---|---|
| `set_timer` | `query` (str), `duration` (int opt) | *"Set a timer for 5 minutes"*, *"Set a 25-minute focus timer"* | Spawns an interactive circular SVG timer widget in the UI with sound alert and desktop notification. |
| `stopwatch` | None | *"Start stopwatch"*, *"Open stopwatch"* | Starts a live countup stopwatch widget with play/pause/reset controls. |
| `set_reminder` | `query` (str) | *"Remind me to call mom in 30 minutes"*, *"Remind me to drink water at 4pm"* | Schedules a background alert that triggers desktop notification and voice reminder. |
| `list_reminders` | None | *"What are my reminders?"*, *"Show my reminders"* | Lists all pending active reminders. |
| `cancel_reminder` | `id` / `message` | *"Cancel the reminder to call mom"* | Cancels matching scheduled reminder. |

---

### 7. Web & Live Telemetry
| Tool Name | Parameters | Spoken/Voice Prompt Examples | What It Does |
|---|---|---|---|
| `get_weather` | `city` (str) | *"What's the weather in London?"*, *"Weather in Tokyo today"* | Fetches live condition, temperature, humidity, wind velocity, and forecasts via `wttr.in`. Renders an atmospheric weather card. |
| `web_search` | `query` (str) | *"Search Google for latest tech news"*, *"Search the web for Python 3.12 features"* | Multi-engine web search (Bing/DuckDuckGo/Google) with LLM snippet synthesis. |
| `open_website` | `url` (str) | *"Open github.com"*, *"Go to reddit.com"* | Opens the specified URL in the user's default web browser. |

---

### 8. System & OS Automation
| Tool Name | Parameters | Spoken/Voice Prompt Examples | What It Does |
|---|---|---|---|
| `window_mgmt` | `action` (str) | *"Minimize all windows"*, *"Maximize window"*, *"Switch window"* | Window management (`minimize_all`, `maximize`, `restore`, `switch_window`). |
| `system_status` | None | *"Check system status"*, *"What is my CPU and RAM usage?"*, *"Battery status"* | Reports CPU load %, RAM usage %, disk space, and battery status. |
| `set_brightness` | `level` (int) | *"Set brightness to 80%"*, *"Dim the screen"* | Adjusts display brightness via WMI. |
| `lock_pc` | None | *"Lock my PC"*, *"Lock computer"* | Locks Windows session (`LockWorkStation`). |
| `sleep_pc` | None | *"Put computer to sleep"*, *"Sleep PC"* | Suspends system to low power sleep mode. |
| `restart_pc` | None | *"Restart computer"* | Initiates controlled system restart (requires confirmation). |
| `cancel_shutdown` | None | *"Cancel shutdown"* | Cancels pending Windows shutdown/restart timer (`shutdown /a`). |
| `empty_recycle_bin` | None | *"Empty recycle bin"* | Empties the Windows desktop Recycle Bin (requires confirmation). |

---

### 9. Keyboard, Mouse & Content Generation
| Tool Name | Parameters | Spoken/Voice Prompt Examples | What It Does |
|---|---|---|---|
| `type_text` | `text` (str), `app` (opt) | *"Type Hello World into Notepad"*, *"Type my email address"* | Focuses the target app or active window and types the given text using `pyautogui`/clipboard. |
| `press_key` | `keys` (str) | *"Press Enter"*, *"Press Ctrl+S"*, *"Press Escape"*, *"Press Tab"* | Simulates individual key presses or hotkey combinations. |
| `click_screen` | `x` (opt), `y` (opt) | *"Click at 500 300"*, *"Click mouse"* | Moves mouse to coordinates and performs a left-click. |
| `new_tab` | None | *"Open new tab"* | Simulates `Ctrl+T` in active browser. |
| `close_tab` | None | *"Close tab"* | Simulates `Ctrl+W` in active browser. |
| `next_tab` / `prev_tab` | None | *"Next tab"*, *"Previous tab"* | Cycles through browser tabs (`Ctrl+Tab` / `Ctrl+Shift+Tab`). |
| `scroll_down` / `scroll_up`| None | *"Scroll down"*, *"Scroll up"* | Scrolls the active document or webpage. |
| `generate_content`| `type` (str), `topic` (str) | *"Draft a leave application"*, *"Write an apology email to client"* | Generates structured content and displays it in the UI review panel. |
| `insert_content` | `content` (str) | *"Insert it"*, *"Paste it here"* | Pastes generated content into the active cursor position via `Ctrl+V`. |

---

### 10. Long-Term Memory, Email & Calendar
| Tool Name | Parameters | Spoken/Voice Prompt Examples | What It Does |
|---|---|---|---|
| `memory_recall` | `query` (str) | *"What is my name?"*, *"What did I say my favorite color was?"* | Semantic search against ChromaDB `user_facts` collection. |
| `unread_emails` | None | *"Check unread emails"*, *"Do I have any new mail?"* | Reads unread emails from local Outlook desktop client. |
| `read_emails` | `count` (opt) | *"Read my latest 3 emails"* | Fetches and summarizes recent Outlook emails. |
| `search_emails` | `query` (str) | *"Search emails from manager"*, *"Find emails about invoice"* | Searches local Outlook inbox by sender or keyword. |
| `draft_email` | `to`, `subject`, `prompt` | *"Draft email to team about Friday meeting"* | Composes an email draft directly in Outlook (never auto-sends). |
| `get_calendar` | `days` (opt) | *"What's on my calendar today?"*, *"Check my schedule"* | Reads Outlook calendar appointments and meetings. |
| `search_calendar` | `query` (str) | *"When is the project review meeting?"* | Searches Outlook calendar events by keyword. |

---

## 🚀 How to Launch Amigo

### Recommended: Launch Web Dashboard
From a terminal in `amigo-main/amigo-main`:
```powershell
py ui_server.py
```
Then navigate to: **`http://localhost:5000`**

### Alternative: Launch Terminal Voice/Keyboard Mode
```powershell
py "amigo main.py"
```

### Global System Wake:
Press **`Alt + V`** anywhere in Windows to trigger voice listening and screen capture without switching windows.
