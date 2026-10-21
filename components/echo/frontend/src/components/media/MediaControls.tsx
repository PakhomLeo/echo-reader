import type { MediaController, MediaSnapshot } from "../../hooks/useMediaController";
import { formatTime } from "../../hooks/useMediaStore";
import { PlaybackRateMenu } from "./PlaybackRateMenu";
import { ProgressSlider } from "./ProgressSlider";

interface MediaControlsProps {
  controller: MediaController;
  snapshot: MediaSnapshot;
}

export function MediaControls({ controller, snapshot }: MediaControlsProps) {
  const playing = snapshot.state === "playing";

  return (
    <div className="media-controls">
      <button
        type="button"
        className="media-button media-play-button"
        onClick={() => controller.togglePlay()}
        aria-label={playing ? "暂停" : "播放"}
      >
        {playing ? "❚❚" : "▶"}
      </button>
      <span className="media-time">{formatTime(snapshot.currentTimeMs)}</span>
      <ProgressSlider controller={controller} snapshot={snapshot} />
      <span className="media-time">{formatTime(snapshot.durationMs)}</span>
      <div className="media-controls-right">
        <button
          type="button"
          className="media-button"
          onClick={() => {
            const next = !snapshot.muted;
            controller.setMuted(next);
          }}
          aria-label={snapshot.muted ? "取消静音" : "静音"}
        >
          {snapshot.muted ? "🔇" : "🔊"}
        </button>
        <input
          type="range"
          className="media-volume"
          min={0}
          max={1}
          step={0.05}
          value={snapshot.volume}
          aria-label="音量"
          onChange={(e) => {
            controller.setVolume(Number(e.target.value));
            if (Number(e.target.value) > 0) {
              controller.setMuted(false);
            }
          }}
        />
        <PlaybackRateMenu controller={controller} snapshot={snapshot} />
      </div>
    </div>
  );
}
