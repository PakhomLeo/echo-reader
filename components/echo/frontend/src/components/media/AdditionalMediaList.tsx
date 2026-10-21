import { useEffect, useRef } from "react";
import type { MediaItemInfo } from "../../api/types";
import type { MediaController } from "../../hooks/useMediaController";
import "./media.css";

function AdditionalMedia({ item, title, controller }: {
  item: MediaItemInfo;
  title: string;
  controller: MediaController;
}) {
  const ref = useRef<HTMLMediaElement>(null);
  useEffect(() => {
    const element = ref.current;
    return element ? controller.registerPeer(element) : undefined;
  }, [controller]);
  const shared = {
    controls: true,
    preload: "metadata" as const,
    src: item.url,
    "aria-label": title,
    onPlay: (event: React.SyntheticEvent<HTMLMediaElement>) => controller.activatePeer(event.currentTarget),
  };
  return item.type === "video"
    ? <video ref={ref as React.RefObject<HTMLVideoElement | null>} {...shared} />
    : <audio ref={ref as React.RefObject<HTMLAudioElement | null>} {...shared} />;
}

export function AdditionalMediaList({ items, controller }: {
  items: MediaItemInfo[];
  controller: MediaController;
}) {
  if (items.length === 0) return null;
  return (
    <section className="additional-media" aria-labelledby="additional-media-title">
      <h2 id="additional-media-title">页面中的其他媒体</h2>
      <div className="additional-media-list">
        {items.map((item, index) => {
          const title = item.title?.trim() || `媒体 ${index + 1}`;
          return <figure className="additional-media-item" key={item.id}>
            <figcaption><strong>{title}</strong>{item.description && <p>{item.description}</p>}</figcaption>
            <AdditionalMedia item={item} title={title} controller={controller} />
          </figure>;
        })}
      </div>
    </section>
  );
}
