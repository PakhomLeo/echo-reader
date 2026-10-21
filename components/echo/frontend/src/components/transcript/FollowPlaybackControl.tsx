interface FollowPlaybackControlProps {
  follow: boolean;
  onToggle: () => void;
}

export function FollowPlaybackControl({
  follow,
  onToggle,
}: FollowPlaybackControlProps) {
  return (
    <button
      type="button"
      className={`follow-button${follow ? " follow-button-active" : ""}`}
      onClick={onToggle}
      aria-pressed={follow}
    >
      跟随高亮
    </button>
  );
}
