import type { MediaController, MediaSnapshot } from "../../hooks/useMediaController";
import type { MediaElementProps } from "../../hooks/useMedia";

interface VideoPlayerProps {
  controller: MediaController;
  src: string;
  mediaProps: MediaElementProps;
  snapshot: MediaSnapshot;
}

export function VideoPlayer({ controller, src, mediaProps, snapshot }: VideoPlayerProps) {
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
    <div className="media-video">
      <video
        {...mediaProps}
        key={src}
        src={src}
        preload="metadata"
        playsInline
        disablePictureInPicture
        onContextMenu={(e) => e.preventDefault()}
      />
      <div className="media-video-overlay">
        {snapshot.state === "loading" && (
          <span className="media-loading">加载中…</span>
        )}
        {snapshot.state === "ended" && (
          <span className="media-loading">播放结束</span>
        )}
      </div>
    </div>
  );
}
