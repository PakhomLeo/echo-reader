import { useRef, useState } from "react";
import type { MediaController, MediaSnapshot } from "../../hooks/useMediaController";

interface ProgressSliderProps {
  controller: MediaController;
  snapshot: MediaSnapshot;
}

export function ProgressSlider({
  controller,
  snapshot,
}: ProgressSliderProps) {
  const [preview, setPreview] = useState<number | null>(null);
  const dragging = useRef(false);

  const current = preview ?? snapshot.currentTimeMs;
  const max = Math.max(snapshot.durationMs, 1);

  const handlePointerDown = () => {
    dragging.current = true;
  };

  const handleCommit = () => {
    dragging.current = false;
    if (preview !== null) {
      controller.seekToMs(preview);
      setPreview(null);
    }
  };

  return (
    <input
      type="range"
      className="media-progress"
      min={0}
      max={max}
      value={current}
      step={50}
      aria-label="播放进度"
      onPointerDown={handlePointerDown}
      onChange={(e) => setPreview(Number(e.target.value))}
      onPointerUp={handleCommit}
      onPointerCancel={handleCommit}
      onBlur={handleCommit}
      onKeyUp={(e) => {
        if (
          e.key === "ArrowLeft" ||
          e.key === "ArrowRight" ||
          e.key === "Home" ||
          e.key === "End"
        ) {
          controller.seekToMs(Number((e.target as HTMLInputElement).value));
          setPreview(null);
        }
      }}
    />
  );
}
