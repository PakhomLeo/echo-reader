import type { MediaInfo } from "../../api/types";
import type { MediaController } from "../../hooks/useMediaController";
import type { MediaElementProps } from "../../hooks/useMedia";
import { useMediaSnapshot } from "../../hooks/useMediaStore";
import { AudioPlayer } from "./AudioPlayer";
import { MediaControls } from "./MediaControls";
import { VideoPlayer } from "./VideoPlayer";
import "./media.css";

interface StickyMediaCardProps {
  media: MediaInfo;
  controller: MediaController;
  mediaProps: MediaElementProps;
}

export function StickyMediaCard({
  media,
  controller,
  mediaProps,
}: StickyMediaCardProps) {
  const snapshot = useMediaSnapshot(controller);
  // 视频仅在播放时置顶；暂停/结束时随页面滚动划走
  const videoSticky = media.type === "video" && snapshot.state === "playing";

  return (
    <section
      className={`media-card media-card-${media.type}${videoSticky ? " media-card-sticky" : ""}`}
      aria-label="媒体播放器"
    >
      {media.type === "video" ? (
        <>
          <VideoPlayer
            controller={controller}
            src={media.url}
            mediaProps={mediaProps}
            snapshot={snapshot}
          />
          <MediaControls
            controller={controller}
            snapshot={snapshot}
          />
        </>
      ) : (
        <AudioPlayer
          controller={controller}
          src={media.url}
          title={media.title}
          mediaProps={mediaProps}
          snapshot={snapshot}
        />
      )}
    </section>
  );
}
