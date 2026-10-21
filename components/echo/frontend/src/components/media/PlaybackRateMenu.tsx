import { useState } from "react";
import {
  PLAYBACK_RATES,
  type MediaController,
  type MediaSnapshot,
} from "../../hooks/useMediaController";

interface PlaybackRateMenuProps {
  controller: MediaController;
  snapshot: MediaSnapshot;
}

export function PlaybackRateMenu({ controller, snapshot }: PlaybackRateMenuProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="media-rate-wrap">
      <button
        type="button"
        className="media-button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="播放速度"
      >
        {snapshot.playbackRate}×
      </button>
      {open && (
        <div className="media-rate-menu" role="menu">
          {PLAYBACK_RATES.map((rate) => (
            <button
              key={rate}
              type="button"
              role="menuitemradio"
              aria-checked={snapshot.playbackRate === rate}
              className={`media-rate-item${snapshot.playbackRate === rate ? " media-rate-item-active" : ""}`}
              onClick={() => {
                controller.setPlaybackRate(rate);
                setOpen(false);
              }}
            >
              {rate}×
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
