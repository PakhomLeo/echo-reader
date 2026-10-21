export type MediaState =
  | "idle" | "loading" | "ready" | "playing" | "paused" | "ended" | "error";

export const PLAYBACK_RATES = [0.75, 1, 1.25, 1.5, 2] as const;
const RATE_KEY = "reader.media.playbackRate";

export interface MediaSnapshot {
  state: MediaState;
  currentTimeMs: number;
  durationMs: number;
  volume: number;
  muted: boolean;
  playbackRate: number;
  error: { code: number | null; message: string | null } | null;
  revision: number;
}

function loadRate(): number {
  try {
    const value = Number(localStorage.getItem(RATE_KEY));
    if ((PLAYBACK_RATES as readonly number[]).includes(value)) return value;
  } catch {
    // Storage is optional.
  }
  return 1;
}

export class MediaController {
  private element: HTMLMediaElement | null = null;
  private readonly listeners = new Set<() => void>();
  private readonly peers = new Set<HTMLMediaElement>();
  private playRetries = 0;
  private snapshot: MediaSnapshot = Object.freeze({
    state: "idle",
    currentTimeMs: 0,
    durationMs: 0,
    volume: 1,
    muted: false,
    playbackRate: loadRate(),
    error: null,
    revision: 0,
  });

  getSnapshot = (): MediaSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private update(changes: Partial<Omit<MediaSnapshot, "revision">>) {
    this.snapshot = Object.freeze({
      ...this.snapshot,
      ...changes,
      revision: this.snapshot.revision + 1,
    });
    this.listeners.forEach((listener) => listener());
  }

  setElement(element: HTMLMediaElement | null) {
    if (element === this.element) return;
    this.element = element;
    if (element) {
      element.volume = this.snapshot.volume;
      element.muted = this.snapshot.muted;
      element.playbackRate = this.snapshot.playbackRate;
    }
    this.update({});
  }

  registerPeer(element: HTMLMediaElement): () => void {
    this.peers.add(element);
    return () => this.peers.delete(element);
  }

  activatePeer(active: HTMLMediaElement) {
    if (this.element && !this.element.paused) this.element.pause();
    this.peers.forEach((peer) => {
      if (peer !== active && !peer.paused) peer.pause();
    });
  }

  updateState(state: MediaState) {
    if (state !== this.snapshot.state) this.update({ state });
  }

  handleLoadedMetadata(duration: number) {
    this.update({ durationMs: duration * 1000, state: "ready" });
  }

  handleTimeUpdate(time: number) {
    this.update({ currentTimeMs: time * 1000 });
  }

  handleEnded() {
    this.update({ state: "ended" });
  }

  handleError() {
    this.update({
      state: "error",
      error: this.element?.error
        ? { code: this.element.error.code, message: this.element.error.message }
        : { code: null, message: null },
    });
  }

  retry() {
    if (!this.element) return;
    this.update({ state: "idle", error: null });
    try {
      this.element.load();
    } catch {
      // Some test/browser media implementations do not expose load().
    }
  }

  reset() {
    if (this.element && !this.element.paused) this.element.pause();
    this.update({ state: "idle", currentTimeMs: 0, durationMs: 0, error: null });
  }

  private playElement() {
    if (!this.element) return;
    this.peers.forEach((peer) => { if (!peer.paused) peer.pause(); });
    void this.element.play().catch((error: unknown) => {
      const name = error instanceof DOMException ? error.name : "";
      if (name === "AbortError" || name === "NotAllowedError") return;
      if (this.playRetries < 3) {
        this.playRetries += 1;
        window.setTimeout(() => this.playElement(), 400);
      } else {
        console.warn("[media] play() failed:", error);
      }
    });
  }

  private safePlay() {
    this.playRetries = 0;
    this.playElement();
  }

  togglePlay() {
    if (!this.element) return;
    if (this.snapshot.state === "playing") this.element.pause();
    else this.safePlay();
  }

  play() {
    if (this.element && this.snapshot.state !== "playing") this.safePlay();
  }

  pause() {
    if (this.element && !this.element.paused) this.element.pause();
  }

  seekToMs(ms: number) {
    if (!this.element || this.snapshot.durationMs <= 0) return;
    const value = Math.min(Math.max(ms, 0), this.snapshot.durationMs);
    this.element.currentTime = value / 1000;
    this.update({ currentTimeMs: value });
  }

  setVolume(volume: number) {
    if (this.element) this.element.volume = volume;
    this.update({ volume });
  }

  setMuted(muted: boolean) {
    if (this.element) this.element.muted = muted;
    this.update({ muted });
  }

  setPlaybackRate(playbackRate: number) {
    if (this.element) this.element.playbackRate = playbackRate;
    try {
      localStorage.setItem(RATE_KEY, String(playbackRate));
    } catch {
      // Storage is optional.
    }
    this.update({ playbackRate });
  }
}
