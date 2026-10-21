import { useSyncExternalStore } from "react";
import type { MediaController } from "./useMediaController";

const EMPTY_SNAPSHOT = Object.freeze({
  state: "idle" as const,
  currentTimeMs: 0,
  durationMs: 0,
  volume: 1,
  muted: false,
  playbackRate: 1,
  error: null,
  revision: 0,
});

export function useMediaSnapshot(controller: MediaController | null) {
  return useSyncExternalStore(
    controller?.subscribe ?? (() => () => undefined),
    controller?.getSnapshot ?? (() => EMPTY_SNAPSHOT),
  );
}

export function formatTime(ms: number): string {
  const total = Math.floor(ms / 1000);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}
