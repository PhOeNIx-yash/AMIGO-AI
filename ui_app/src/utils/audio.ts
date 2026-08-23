/**
 * Audio Utility Module for Amigo Voice Assistant.
 * Sound effects disabled for silent operation.
 */

class SoundEffects {
  public playClick() {}
  public playWakeChime() {}
  public playSubmit() {}
  public playSuccess() {}
  public playAlarm() {}
  public playMicOn() {}
  public playMicOff() {}
  public playWorkingPulse() {}
  public setEnabled(_val: boolean) {}
  public isEnabled() {
    return false;
  }
  public setVolume(_vol: number) {}
  public getVolume() {
    return 0;
  }
}

export const sfx = new SoundEffects();

// Amigo Local Neural TTS Engine (Kokoro ONNX)
export async function speakText(text: string, onEnd?: () => void) {
  if (!text || !text.trim()) {
    if (onEnd) onEnd();
    return;
  }

  const cleanText = text.trim();

  try {
    const res = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: cleanText }),
    });
    if (!res.ok) {
      // Fallback if accessed from separate dev port
      await fetch("http://127.0.0.1:5000/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: cleanText }),
      });
    }
  } catch (err) {
    try {
      await fetch("http://127.0.0.1:5000/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: cleanText }),
      });
    } catch (e2) {
      console.warn("[Amigo TTS Error]:", e2);
    }
  } finally {
    if (onEnd) setTimeout(onEnd, 1200);
  }
}
