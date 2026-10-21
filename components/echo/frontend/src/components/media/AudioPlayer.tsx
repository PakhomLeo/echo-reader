import type { MediaController, MediaSnapshot } from "../../hooks/useMediaController";
import type { MediaElementProps } from "../../hooks/useMedia";
import { MediaControls } from "./MediaControls";

interface AudioPlayerProps {
  controller: MediaController;
  src: string;
  title: string | null;
  mediaProps: MediaElementProps;
  snapshot: MediaSnapshot;
}

export function AudioPlayer({
  controller,
  src,
  title,
  mediaProps,
  snapshot,
}: AudioPlayerProps) {
  if (snapshot.state === "error") {
    const err = snapshot.error;
    return (
      <div className="media-error">
        <p>无法播放该媒体文件</p>
        <p className="media-error-hint">文件可能不存在或编码不受浏览器支持</p>
        {err?.code != null && (
          <p className="media-error-code" data-testid="media-error-code">
            错误码 {err.code}
            {err.message ? `：${err.message}` : ""}
          </p>
        )}
        <button
          type="button"
          className="media-error-retry"
          onClick={() => controller.retry()}
        >
          重试
        </button>
      </div>
    );
  }

  return (
    <div className="media-audio">
      <audio {...mediaProps} key={src} src={src} preload="metadata" />
      <div className="media-audio-title">{title ?? "音频"}</div>
      <MediaControls
        controller={controller}
        snapshot={snapshot}
      />
    </div>
  );
}
