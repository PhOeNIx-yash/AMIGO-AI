// Zero-overhead high performance audio frequency bus for 60fps canvas & visualizers
// Avoids React virtual DOM reconciliation cycles on high-frequency audio analysis ticks

type AudioListener = (level: number, frequencyData?: Uint8Array) => void;

class AudioBus {
  private level: number = 0;
  private listeners: Set<AudioListener> = new Set();

  public emit(level: number, frequencyData?: Uint8Array) {
    this.level = level;
    this.listeners.forEach((listener) => listener(level, frequencyData));
  }

  public getLevel(): number {
    return this.level;
  }

  public subscribe(listener: AudioListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
}

export const audioBus = new AudioBus();
