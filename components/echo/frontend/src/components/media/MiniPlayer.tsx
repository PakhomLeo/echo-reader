import type { MediaController } from "../../hooks/useMediaController";
import { formatTime, useMediaSnapshot } from "../../hooks/useMediaStore";
import { ProgressSlider } from "./ProgressSlider";

interface MiniPlayerProps {
  controller: MediaController;
}

/** 音频的全局迷你播放栏：固定页面底部，圆角矩形，可播放/暂停/拖动进度。 */
export function MiniPlayer({ controller }: MiniPlayerProps) {
  const snapshot = useMediaSnapshot(controller);
  const playing = snapshot.state === "playing";

  return (
    <div className="mini-player" aria-label="迷你播放器">
      <button
        type="button"
        className="media-play-button mini-player-play"
        onClick={() => controller.togglePlay()}
        aria-label={playing ? "暂停" : "播放"}
      >
        {playing ? "❚❚" : "▶"}
      </button>
      <span className="media-time">{formatTime(snapshot.currentTimeMs)}</span>
      <ProgressSlider
        controller={controller}
        snapshot={snapshot}
      />
      <span className="media-time">{formatTime(snapshot.durationMs)}</span>
    </div>
  );
}
