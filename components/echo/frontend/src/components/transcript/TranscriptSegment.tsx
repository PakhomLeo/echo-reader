import type { KeyboardEvent } from "react";
import type { TranscriptSegment as Segment } from "../../api/types";
import { formatTimestamp } from "./format";

interface TranscriptSegmentProps {
  segment: Segment;
  active: boolean;
  editing: boolean;
  draft: { speaker: string; text: string } | null;
  textareaRef: (el: HTMLTextAreaElement | null) => void;
  onSpeakerChange: (id: string, speaker: string) => void;
  onTextChange: (id: string, text: string) => void;
  onCancelEdit: (id: string) => void;
  onMoveFocus: (from: string, direction: 1 | -1) => void;
  onSaveAll: () => void;
  onTimestampClick: (segment: Segment) => void;
}

export function TranscriptSegment({
  segment,
  active,
  editing,
  draft,
  textareaRef,
  onSpeakerChange,
  onTextChange,
  onCancelEdit,
  onMoveFocus,
  onSaveAll,
  onTimestampClick,
}: TranscriptSegmentProps) {
  const speaker = draft?.speaker ?? segment.speaker;
  const text = draft?.text ?? segment.text;

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Tab") {
      e.preventDefault();
      onMoveFocus(segment.id, e.shiftKey ? -1 : 1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancelEdit(segment.id);
    } else if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      onSaveAll();
    }
  };

  return (
    <div
      className={`transcript-segment${active ? " transcript-segment-active" : ""}`}
      data-segment-id={segment.id}
    >
      <button
        type="button"
        className="transcript-timestamp"
        onClick={() => onTimestampClick(segment)}
        title="跳转到该时间并播放"
      >
        {formatTimestamp(segment.start_ms)}
      </button>
      {editing ? (
        <div className="transcript-segment-body">
          <input
            className="transcript-speaker-input"
            value={speaker}
            onChange={(e) => onSpeakerChange(segment.id, e.target.value)}
            aria-label="说话人"
          />
          <textarea
            className="transcript-textarea"
            value={text}
            rows={1}
            onChange={(e) => {
              e.target.style.height = "auto";
              e.target.style.height = `${e.target.scrollHeight}px`;
              onTextChange(segment.id, e.target.value);
            }}
            onKeyDown={handleKeyDown}
            ref={textareaRef}
            aria-label="转录文字"
          />
        </div>
      ) : (
        <div className="transcript-segment-body">
          <span className="transcript-speaker">{segment.speaker}</span>
          <span className="transcript-text">{segment.text}</span>
        </div>
      )}
    </div>
  );
}
