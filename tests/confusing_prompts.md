# Amigo Confusion Test Set

Use these prompts to stress the routing and clarification logic.

1. "Open the weather app and search for the weather in Paris."
2. "Play the song from that article and also open Spotify."
3. "Can you look up my last search and tell me the weather in Tokyo?"
4. "Close Chrome and search the web for the best browser to use."
5. "Open the file for the report and tell me what it says."
6. "What time is it and what does the calendar say for tomorrow?"
7. "Search for the market and open the market app."
8. "Pause media and tell me what song is playing."
9. "Open the weather app for me please, but also google the weather."
10. "Find the document and open my notes on it."

Expected behavior:
- Clear, single-intent requests should route directly without clarification.
- Mixed or coupled intents should trigger the clarification path.
- The response should include clear candidate actions and let the user resolve the ambiguity.
