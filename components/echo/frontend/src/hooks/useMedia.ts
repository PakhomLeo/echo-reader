import { useEffect, useState } from "react";
import { MediaController } from "./useMediaController";

export interface MediaElementProps {
  ref: (el: HTMLMediaElement | null) => void;
  onLoadedMetadata: (e: React.SyntheticEvent<HTMLMediaElement>) => void;
  onTimeUpdate: (e: React.SyntheticEvent<HTMLMediaElement>) => void;
  onPlay: () => void;
  onPause: () => void;
  onEnded: () => void;
  onError: () => void;
}

interface UseMediaControllerResult {
  controller: MediaController;
  mediaProps: MediaElementProps;
}

export function useMediaController(): UseMediaControllerResult {
  const [controller] = useState(() => new MediaController());

  useEffect(() => {
    return () => controller.setElement(null);
  }, [controller]);

  return {
    controller,
    mediaProps: {
      ref: (el) => controller.setElement(el),
      onLoadedMetadata: (e) =>
        controller.handleLoadedMetadata(e.currentTarget.duration),
      onTimeUpdate: (e) =>
        controller.handleTimeUpdate(e.currentTarget.currentTime),
      onPlay: () => controller.updateState("playing"),
      onPause: () => controller.updateState("paused"),
      onEnded: () => controller.handleEnded(),
      onError: () => controller.handleError(),
    },
  };
}
