"""
Held-Out Evaluation Set for Amigo Voice Assistant.
Split into DEV_EVAL_DATA (for development tuning) and SEALED_EVAL_DATA (for gate verification).
Never copy, paraphrase, or leak any of these strings into system prompts, tool schemas, or examples.
"""

# Base capabilities data
_RAW_EVAL_DATA = [
    # ── 1. CLOCK TIME (get_time) ──
    {"query": "Could you kindly inform me of the current hour?", "expected_tool": "get_time", "category": "formal"},
    {"query": "what time you got", "expected_tool": "get_time", "category": "casual"},
    {"query": "time now", "expected_tool": "get_time", "category": "short"},
    {"query": "hey buddy I was just wondering if you could glance at the clock and tell me what time it is right now", "expected_tool": "get_time", "category": "rambling"},
    {"query": "what time is it right now no question mark", "expected_tool": "get_time", "category": "stt_error"},
    {"query": "current local time please", "expected_tool": "get_time", "category": "formal"},
    {"query": "what hour is it", "expected_tool": "get_time", "category": "casual"},
    {"query": "clock check", "expected_tool": "get_time", "category": "short"},
    {"query": "tell me the time on the pc", "expected_tool": "get_time", "category": "casual"},
    {"query": "do you have the current time handy", "expected_tool": "get_time", "category": "casual"},

    # ── 2. DATE (get_date) ──
    {"query": "Pray tell, what is today's calendar date?", "expected_tool": "get_date", "category": "formal"},
    {"query": "whats todays date", "expected_tool": "get_date", "category": "casual"},
    {"query": "date today", "expected_tool": "get_date", "category": "short"},
    {"query": "I seem to have lost track of the days this week could you tell me what date it is today", "expected_tool": "get_date", "category": "rambling"},
    {"query": "what is today date on the calendar", "expected_tool": "get_date", "category": "stt_error"},
    {"query": "which day of the month are we on", "expected_tool": "get_date", "category": "casual"},
    {"query": "todays date please", "expected_tool": "get_date", "category": "short"},
    {"query": "what day is it today exactly", "expected_tool": "get_date", "category": "casual"},
    {"query": "give me the current date", "expected_tool": "get_date", "category": "formal"},
    {"query": "check calendar date for today", "expected_tool": "get_date", "category": "casual"},

    # ── 3. OPEN APP (open_app) ──
    {"query": "Would you please launch the Spotify software?", "expected_tool": "open_app", "category": "formal"},
    {"query": "fire up calculator", "expected_tool": "open_app", "category": "casual"},
    {"query": "notepad", "expected_tool": "open_app", "category": "short"},
    {"query": "hey if you have a moment can you go ahead and start up the visual studio code application for me", "expected_tool": "open_app", "category": "rambling"},
    {"query": "open crome browser right now", "expected_tool": "open_app", "category": "stt_error"},
    {"query": "bring up the terminal", "expected_tool": "open_app", "category": "casual"},
    {"query": "launch vlc player", "expected_tool": "open_app", "category": "casual"},
    {"query": "start discord", "expected_tool": "open_app", "category": "casual"},
    {"query": "run paint app", "expected_tool": "open_app", "category": "casual"},
    {"query": "pull up excel spreadsheet app", "expected_tool": "open_app", "category": "casual"},

    # ── 4. CLOSE APP (close_app) ──
    {"query": "Please terminate the active Notepad session.", "expected_tool": "close_app", "category": "formal"},
    {"query": "kill spotify", "expected_tool": "close_app", "category": "casual"},
    {"query": "close calc", "expected_tool": "close_app", "category": "short"},
    {"query": "I am completely finished working in discord so could you please shut down that window", "expected_tool": "close_app", "category": "rambling"},
    {"query": "shut down chrome window please", "expected_tool": "close_app", "category": "stt_error"},
    {"query": "exit vlc player", "expected_tool": "close_app", "category": "casual"},
    {"query": "quit the calculator app", "expected_tool": "close_app", "category": "casual"},
    {"query": "terminate visual studio code", "expected_tool": "close_app", "category": "formal"},
    {"query": "dismiss the paint program", "expected_tool": "close_app", "category": "casual"},
    {"query": "close out of steam", "expected_tool": "close_app", "category": "casual"},

    # ── 5. PLAY YOUTUBE (play_youtube) ──
    {"query": "Kindly initiate playback of Clair de Lune on YouTube.", "expected_tool": "play_youtube", "category": "formal"},
    {"query": "put on some lo fi beats", "expected_tool": "play_youtube", "category": "casual"},
    {"query": "play coldplay yellow", "expected_tool": "play_youtube", "category": "short"},
    {"query": "could you find and start playing that song bohemian rhapsody by queen over on youtube", "expected_tool": "play_youtube", "category": "rambling"},
    {"query": "play hotel california on you tube", "expected_tool": "play_youtube", "category": "stt_error"},
    {"query": "stream ed sheeran shape of you", "expected_tool": "play_youtube", "category": "casual"},
    {"query": "play relaxing acoustic guitar music", "expected_tool": "play_youtube", "category": "casual"},
    {"query": "blast some rock music on youtube", "expected_tool": "play_youtube", "category": "casual"},
    {"query": "put on interstellar soundtrack", "expected_tool": "play_youtube", "category": "casual"},
    {"query": "cue up billie eilish bad guy", "expected_tool": "play_youtube", "category": "casual"},

    # ── 6. MEDIA PLAYBACK CONTROL (pause_media / resume_media / next_track / prev_track / current_media) ──
    {"query": "hold the music for a sec", "expected_tool": "pause_media", "category": "casual"},
    {"query": "keep playing the track", "expected_tool": "resume_media", "category": "casual"},
    {"query": "skip to the following song", "expected_tool": "next_track", "category": "formal"},
    {"query": "go back to the prior track", "expected_tool": "prev_track", "category": "formal"},
    {"query": "what track is this playing right now", "expected_tool": "current_media", "category": "casual"},
    {"query": "freeze playback", "expected_tool": "pause_media", "category": "casual"},
    {"query": "unpause audio", "expected_tool": "resume_media", "category": "short"},
    {"query": "skip this one", "expected_tool": "next_track", "category": "short"},
    {"query": "play the previous one again", "expected_tool": "prev_track", "category": "casual"},
    {"query": "identify current playing music", "expected_tool": "current_media", "category": "formal"},

    # ── 7. VOLUME CONTROL (set_volume) ──
    {"query": "Adjust the output gain to thirty percent.", "expected_tool": "set_volume", "category": "formal"},
    {"query": "silence the speakers", "expected_tool": "set_volume", "category": "casual"},
    {"query": "louder", "expected_tool": "set_volume", "category": "short"},
    {"query": "it is a bit too loud in here could you soften the sound level a notch", "expected_tool": "set_volume", "category": "rambling"},
    {"query": "turn volume up ten percent", "expected_tool": "set_volume", "category": "stt_error"},
    {"query": "unmute audio", "expected_tool": "set_volume", "category": "short"},
    {"query": "drop sound down a bit", "expected_tool": "set_volume", "category": "casual"},
    {"query": "crank up the audio", "expected_tool": "set_volume", "category": "casual"},
    {"query": "set sound level to eighty", "expected_tool": "set_volume", "category": "casual"},
    {"query": "kill the sound completely", "expected_tool": "set_volume", "category": "casual"},

    # ── 8. SYSTEM POWER & LOCK (lock_pc / sleep_pc / restart_pc / system_status) ──
    {"query": "Secure the workstation immediately.", "expected_tool": "lock_pc", "category": "formal"},
    {"query": "lock workstation", "expected_tool": "lock_pc", "category": "short"},
    {"query": "go to sleep now computer", "expected_tool": "sleep_pc", "category": "casual"},
    {"query": "suspend system state", "expected_tool": "sleep_pc", "category": "formal"},
    {"query": "reboot the machine please", "expected_tool": "restart_pc", "category": "casual"},
    {"query": "perform system restart", "expected_tool": "restart_pc", "category": "formal"},
    {"query": "how much battery and ram is left", "expected_tool": "system_status", "category": "casual"},
    {"query": "hardware metrics check", "expected_tool": "system_status", "category": "short"},
    {"query": "purge deleted files from trash bin", "expected_tool": "empty_recycle_bin", "category": "formal"},
    {"query": "clean out the recycle bin", "expected_tool": "empty_recycle_bin", "category": "casual"},

    # ── 9. WINDOW MANAGEMENT (window_mgmt) ──
    {"query": "Collapse all active applications to the taskbar.", "expected_tool": "window_mgmt", "category": "formal"},
    {"query": "hide every window", "expected_tool": "window_mgmt", "category": "casual"},
    {"query": "show desktop", "expected_tool": "window_mgmt", "category": "short"},
    {"query": "make this window take the entire screen", "expected_tool": "window_mgmt", "category": "casual"},
    {"query": "fullscreen current window", "expected_tool": "window_mgmt", "category": "short"},
    {"query": "minimize everything on screen", "expected_tool": "window_mgmt", "category": "casual"},
    {"query": "expand active window to maximum size", "expected_tool": "window_mgmt", "category": "formal"},
    {"query": "un-maximize current window", "expected_tool": "window_mgmt", "category": "casual"},
    {"query": "minimize all programs", "expected_tool": "window_mgmt", "category": "casual"},
    {"query": "restore minimized windows", "expected_tool": "window_mgmt", "category": "casual"},

    # ── 10. SCREENSHOT & VISION (take_screenshot / read_screen) ──
    {"query": "Capture the visual display to image.", "expected_tool": "take_screenshot", "category": "formal"},
    {"query": "grab a screen snap", "expected_tool": "take_screenshot", "category": "casual"},
    {"query": "screenshot", "expected_tool": "take_screenshot", "category": "short"},
    {"query": "hey could you take a picture of what is currently on my desktop monitor", "expected_tool": "take_screenshot", "category": "rambling"},
    {"query": "snap the screen", "expected_tool": "take_screenshot", "category": "short"},
    {"query": "what error message is showing on my screen right now", "expected_tool": "read_screen", "category": "casual"},
    {"query": "inspect the visual layout of this window", "expected_tool": "read_screen", "category": "formal"},
    {"query": "read text displayed on my monitor", "expected_tool": "read_screen", "category": "casual"},
    {"query": "look at what is on the screen and tell me the title", "expected_tool": "read_screen", "category": "casual"},
    {"query": "examine screen contents", "expected_tool": "read_screen", "category": "short"},

    # ── 11. WEATHER (get_weather) ──
    {"query": "Could you provide the meteorological conditions for Chicago?", "expected_tool": "get_weather", "category": "formal"},
    {"query": "is it pouring in seattle", "expected_tool": "get_weather", "category": "casual"},
    {"query": "weather miami", "expected_tool": "get_weather", "category": "short"},
    {"query": "I am thinking about taking a walk outside do you know if it is cold or sunny in Boston today", "expected_tool": "get_weather", "category": "rambling"},
    {"query": "temperature in san francisco today", "expected_tool": "get_weather", "category": "stt_error"},
    {"query": "how hot is it in phoenix right now", "expected_tool": "get_weather", "category": "casual"},
    {"query": "will it snow in denver this evening", "expected_tool": "get_weather", "category": "casual"},
    {"query": "toronto weather forecast", "expected_tool": "get_weather", "category": "short"},
    {"query": "check outside temperature in berlin", "expected_tool": "get_weather", "category": "casual"},
    {"query": "what are the weather conditions in sydney", "expected_tool": "get_weather", "category": "formal"},

    # ── 12. COUNTDOWN TIMER (set_timer) ──
    {"query": "Initialize a countdown mechanism for twelve minutes.", "expected_tool": "set_timer", "category": "formal"},
    {"query": "give me a timer for four minutes", "expected_tool": "set_timer", "category": "casual"},
    {"query": "timer 45 seconds", "expected_tool": "set_timer", "category": "short"},
    {"query": "I am boiling some pasta on the stove so please start a countdown for eight minutes", "expected_tool": "set_timer", "category": "rambling"},
    {"query": "start a timer for ten mins pizza", "expected_tool": "set_timer", "category": "stt_error"},
    {"query": "countdown 3 minutes for tea", "expected_tool": "set_timer", "category": "casual"},
    {"query": "set 20 minute timer", "expected_tool": "set_timer", "category": "short"},
    {"query": "start a stopwatch timer for 15 minutes", "expected_tool": "set_timer", "category": "casual"},
    {"query": "set a buzzer for thirty seconds", "expected_tool": "set_timer", "category": "casual"},
    {"query": "timer for one hour laundry", "expected_tool": "set_timer", "category": "casual"},

    # ── 13. TASK REMINDER (set_reminder) ──
    {"query": "Schedule a notification for 4:00 PM to submit the quarterly report.", "expected_tool": "set_reminder", "category": "formal"},
    {"query": "alert me to take my medicine in an hour", "expected_tool": "set_reminder", "category": "casual"},
    {"query": "remind me buy milk tomorrow 9am", "expected_tool": "set_reminder", "category": "short"},
    {"query": "hey make sure you ping me at five thirty this evening because I promised I would pick up Sarah", "expected_tool": "set_reminder", "category": "rambling"},
    {"query": "remind me call doctor at two pm", "expected_tool": "set_reminder", "category": "stt_error"},
    {"query": "set a reminder for tomorrow morning to check emails", "expected_tool": "set_reminder", "category": "casual"},
    {"query": "create a reminder to pay electric bill on Friday", "expected_tool": "set_reminder", "category": "casual"},
    {"query": "notify me to stretch in 45 minutes", "expected_tool": "set_reminder", "category": "casual"},
    {"query": "remind me about the team sync at 3 pm", "expected_tool": "set_reminder", "category": "casual"},
    {"query": "prompt me to drink water in twenty minutes", "expected_tool": "set_reminder", "category": "casual"},

    # ── 14. WINDOWS SETTINGS (open_settings / change_setting / get_setting) ──
    {"query": "Navigate to the Bluetooth configuration panel.", "expected_tool": "open_settings", "category": "formal"},
    {"query": "open wifi page", "expected_tool": "open_settings", "category": "casual"},
    {"query": "deactivate bluetooth radio", "expected_tool": "change_setting", "category": "formal"},
    {"query": "turn wifi off", "expected_tool": "change_setting", "category": "short"},
    {"query": "enable dark mode theme", "expected_tool": "change_setting", "category": "casual"},
    {"query": "switch on night light", "expected_tool": "change_setting", "category": "casual"},
    {"query": "is my bluetooth turned on right now", "expected_tool": "get_setting", "category": "casual"},
    {"query": "check current night light state", "expected_tool": "get_setting", "category": "formal"},
    {"query": "bring up display settings window", "expected_tool": "open_settings", "category": "casual"},
    {"query": "toggle wifi on", "expected_tool": "change_setting", "category": "short"},

    # ── 15. EMAIL & CALENDAR (unread_emails / get_calendar / draft_email) ──
    {"query": "Review unread correspondence in my inbox.", "expected_tool": "unread_emails", "category": "formal"},
    {"query": "do i have new emails", "expected_tool": "unread_emails", "category": "casual"},
    {"query": "check new mail", "expected_tool": "unread_emails", "category": "short"},
    {"query": "Inspect upcoming schedule entries for the week.", "expected_tool": "get_calendar", "category": "formal"},
    {"query": "what meetings do i have today", "expected_tool": "get_calendar", "category": "casual"},
    {"query": "calendar agenda", "expected_tool": "get_calendar", "category": "short"},
    {"query": "compose an email to Alex about the budget meeting", "expected_tool": "draft_email", "category": "casual"},
    {"query": "draft a message to boss regarding sick leave", "expected_tool": "draft_email", "category": "casual"},
    {"query": "what appointments are lined up tomorrow", "expected_tool": "get_calendar", "category": "casual"},
    {"query": "any unread messages in outlook", "expected_tool": "unread_emails", "category": "casual"},

    # ── 16. WEB SEARCH (web_search) ──
    {"query": "Research recent geopolitical events in South America.", "expected_tool": "web_search", "category": "formal"},
    {"query": "who won the cricket match yesterday", "expected_tool": "web_search", "category": "casual"},
    {"query": "latest space x launch news", "expected_tool": "web_search", "category": "short"},
    {"query": "can you search online to see what time the supermarket down the street closes tonight", "expected_tool": "web_search", "category": "rambling"},
    {"query": "look up who won best picture oscar this year", "expected_tool": "web_search", "category": "casual"},
    {"query": "find online who is the current ceo of microsoft", "expected_tool": "web_search", "category": "casual"},
    {"query": "query online the valuation of apple stock", "expected_tool": "web_search", "category": "casual"},
    {"query": "what is the current population of tokyo", "expected_tool": "web_search", "category": "casual"},
    {"query": "lookup recipe for authentic chicken tikka masala", "expected_tool": "web_search", "category": "casual"},
    {"query": "search online for python async tutorial", "expected_tool": "web_search", "category": "casual"},

    # ── 17. CHIT-CHAT, REASONING & EXPANDED META-QUESTIONS (chat - MUST NOT TRIGGER A TOOL) ──
    {"query": "Greetings, how fares your operation this afternoon?", "expected_tool": "chat", "category": "chitchat"},
    {"query": "sup amigo how you doing", "expected_tool": "chat", "category": "chitchat"},
    {"query": "what is your favorite color if you had one", "expected_tool": "chat", "category": "chitchat"},
    {"query": "can you compose a whimsical haiku regarding autumn leaves", "expected_tool": "chat", "category": "chitchat"},
    {"query": "If a tree falls in an uninhabited forest does it make a sound?", "expected_tool": "chat", "category": "chitchat"},
    {"query": "why do cats purr when they are happy", "expected_tool": "chat", "category": "chitchat"},
    {"query": "explain how photosynthesis converts sunlight into glucose", "expected_tool": "chat", "category": "chitchat"},
    {"query": "tell me an amusing anecdote about computers", "expected_tool": "chat", "category": "chitchat"},
    {"query": "I am feeling quite tired after a long workday", "expected_tool": "chat", "category": "chitchat"},
    {"query": "what is the meaning of life according to Douglas Adams", "expected_tool": "chat", "category": "chitchat"},
    # Meta-questions (must route to chat, zero tools)
    {"query": "what can you do", "expected_tool": "chat", "category": "meta"},
    {"query": "list your tools", "expected_tool": "chat", "category": "meta"},
    {"query": "what tools do you have available", "expected_tool": "chat", "category": "meta"},
    {"query": "are you online right now", "expected_tool": "chat", "category": "meta"},
    {"query": "who created you and how were you built", "expected_tool": "chat", "category": "meta"},
    {"query": "what model are you running on this machine", "expected_tool": "chat", "category": "meta"},
    {"query": "tell me your capabilities", "expected_tool": "chat", "category": "meta"},
    {"query": "what functions can you perform", "expected_tool": "chat", "category": "meta"},
    {"query": "how do you work", "expected_tool": "chat", "category": "meta"},
    {"query": "are you an ai or a real person", "expected_tool": "chat", "category": "meta"},
    {"query": "what are your features", "expected_tool": "chat", "category": "meta"},
    {"query": "can you help me on my computer", "expected_tool": "chat", "category": "meta"},
    {"query": "explain what you are capable of doing", "expected_tool": "chat", "category": "meta"},
    {"query": "show me your list of functions", "expected_tool": "chat", "category": "meta"},
    {"query": "what is your prompt", "expected_tool": "chat", "category": "meta"},
    {"query": "tell me a secret about yourself", "expected_tool": "chat", "category": "meta"},
    # Bare name & vocatives (must route to chat with direct greeting, zero tools)
    {"query": "Amigo", "expected_tool": "chat", "category": "vocative"},
    {"query": "Hey Amigo", "expected_tool": "chat", "category": "vocative"},
    {"query": "Amigo!", "expected_tool": "chat", "category": "vocative"},
    {"query": "hello amigo", "expected_tool": "chat", "category": "vocative"},

    # ── 18. CONTEXT-DEPENDENT FOLLOW-UPS (history-dependent) ──
    {
        "query": "turn it up higher",
        "expected_tool": "set_volume",
        "category": "follow_up",
        "history": [{"user": "set volume to 40", "assistant": "Volume set to 40%."}]
    },
    {
        "query": "mute it now",
        "expected_tool": "set_volume",
        "category": "follow_up",
        "history": [{"user": "play yellow by coldplay", "assistant": "Playing Yellow on YouTube."}]
    },
    {
        "query": "pause that",
        "expected_tool": "pause_media",
        "category": "follow_up",
        "history": [{"user": "play some jazz", "assistant": "Playing jazz."}]
    },
    {
        "query": "unpause it",
        "expected_tool": "resume_media",
        "category": "follow_up",
        "history": [{"user": "pause the song", "assistant": "Paused playback."}]
    },
    {
        "query": "skip to the next one",
        "expected_tool": "next_track",
        "category": "follow_up",
        "history": [{"user": "what song is this", "assistant": "Currently playing Imagine."}]
    },
    {
        "query": "close it please",
        "expected_tool": "close_app",
        "category": "follow_up",
        "history": [{"user": "open notepad", "assistant": "Opened Notepad."}]
    },
    {
        "query": "turn that back on",
        "expected_tool": "change_setting",
        "category": "follow_up",
        "history": [{"user": "turn off wifi", "assistant": "Wi-Fi turned off."}]
    },
    {
        "query": "what about tomorrow in that city",
        "expected_tool": "get_weather",
        "category": "follow_up",
        "history": [{"user": "what is the weather in Chicago", "assistant": "In Chicago it is 55°F."}]
    },
    {
        "query": "cancel that reminder",
        "expected_tool": "cancel_reminder",
        "category": "follow_up",
        "history": [{"user": "remind me to call mom at 5", "assistant": "Reminder set for 5:00 PM."}]
    },
    {
        "query": "tell me more about that",
        "expected_tool": "chat",
        "category": "follow_up",
        "history": [{"user": "explain quantum computing", "assistant": "Quantum computing uses qubits..."}]
    },
]

# Split evenly into DEV (tuning) and SEALED (verification only)
DEV_EVAL_DATA = [item for i, item in enumerate(_RAW_EVAL_DATA) if i % 2 == 0]
SEALED_EVAL_DATA = [item for i, item in enumerate(_RAW_EVAL_DATA) if i % 2 != 0]
HELD_OUT_EVAL_DATA = _RAW_EVAL_DATA
