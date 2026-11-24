import { runtimeConfig } from "../config/runtimeConfig";

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

// Amigo Local Neural TTS Engine (Supertonic-3 44.1kHz)
export async function speakText(text: string, onEnd?: () => void) {
  if (!text || !text.trim()) {
    if (onEnd) onEnd();
    return;
  }

  const cleanText = text.trim();
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), runtimeConfig.apiTimeoutMs);

  try {
    const res = await fetch(runtimeConfig.speakEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: cleanText }),
      signal: controller.signal,
    });
    if (!res.ok) {
      console.warn(`[Amigo TTS] Speak returned status ${res.status}`);
    }
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === "AbortError") {
      console.warn("[Amigo TTS] Request timed out");
    } else {
      console.warn("[Amigo TTS Error]:", err);
    }
  } finally {
    clearTimeout(timeoutId);
    if (onEnd) {
      setTimeout(onEnd, 1200);
    }
  }
}
