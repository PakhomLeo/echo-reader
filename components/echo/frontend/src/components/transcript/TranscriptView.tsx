import { useCallback, useEffect, useRef, useState } from "react";
import { patchTranscript } from "../../api/items";
import type { TranscriptData } from "../../api/types";
import type { MediaController } from "../../hooks/useMediaController";
import { findActiveSegment } from "./findActiveSegment";
import { TranscriptSegment } from "./TranscriptSegment";
import "./transcript.css";

interface TranscriptViewProps {
  itemId: string;
  data: TranscriptData;
  controller: MediaController | null;
  follow: boolean;
  onReload: () => void;
}

interface Draft {
  speaker: string;
  text: string;
}

type SaveStatus = "idle" | "saving" | "saved" | "conflict" | "error";

export function TranscriptView({
  itemId,
  data,
  controller,
  follow,
  onReload,
}: TranscriptViewProps) {
  const [activeSegmentId, setActiveSegmentId] = useState<string | null>(null);
  const [suspended, setSuspended] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const programmaticScrollRef = useRef(false);
  const scrollTimerRef = useRef<number | null>(null);

  const [editing, setEditing] = useState(false);
  const [drafts, setDrafts] = useState<Map<string, Draft>>(new Map());
  const [latestVersion, setLatestVersion] = useState(data.version);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [segments, setSegments] = useState(data.segments);
  const textareaRefs = useRef<(HTMLTextAreaElement | null)[]>([]);
  const segmentOrder = useRef<string[]>(segments.map((s) => s.id));

  useEffect(() => {
    setLatestVersion(data.version);
    setSegments(data.segments);
    segmentOrder.current = data.segments.map((s) => s.id);
  }, [data]);

  const activeRef = useRef(activeSegmentId);
  activeRef.current = activeSegmentId;
  const followRef = useRef(follow);
  followRef.current = follow;
  const suspendedRef = useRef(suspended);
  suspendedRef.current = suspended;

  // 订阅媒体时间：仅在 activeSegmentId 变化时更新状态
  useEffect(() => {
    if (!controller) return;
    return controller.subscribe(() => {
      const seg = findActiveSegment(segments, controller.getSnapshot().currentTimeMs);
      const next = seg ? seg.id : null;
      if (next !== activeRef.current) {
        setActiveSegmentId(next);
      }
    });
  }, [controller, segments]);

  const scrollActiveIntoView = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const el = container.querySelector<HTMLElement>(
      `.transcript-segment-active`,
    );
    if (!el) return;
    programmaticScrollRef.current = true;
    if (scrollTimerRef.current !== null) {
      window.clearTimeout(scrollTimerRef.current);
    }
    const reduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    el.scrollIntoView({
      block: "center",
      behavior: reduced ? "auto" : "smooth",
    });
    scrollTimerRef.current = window.setTimeout(
      () => {
        programmaticScrollRef.current = false;
        scrollTimerRef.current = null;
      },
      reduced ? 120 : 450,
    );
  }, []);

  useEffect(() => {
    if (followRef.current && !suspendedRef.current) {
      scrollActiveIntoView();
    }
  }, [activeSegmentId, scrollActiveIntoView]);

  const handleScroll = useCallback(() => {
    if (programmaticScrollRef.current) return;
    if (followRef.current && !suspendedRef.current) {
      setSuspended(true);
    }
  }, []);

  const handleTimestampClick = useCallback(
    (segment: { id: string; start_ms: number }) => {
      setActiveSegmentId(segment.id);
      if (controller) {
        controller.seekToMs(segment.start_ms);
        controller.play();
      }
      if (followRef.current && !suspendedRef.current) {
        scrollActiveIntoView();
      }
    },
    [controller, scrollActiveIntoView],
  );

  const handleResume = useCallback(() => {
    setSuspended(false);
    scrollActiveIntoView();
  }, [scrollActiveIntoView]);

  // ---- 编辑模式 ----

  const setDraft = useCallback((id: string, draft: Draft | null) => {
    setDrafts((prev) => {
      const next = new Map(prev);
      if (draft === null) {
        next.delete(id);
      } else {
        next.set(id, draft);
      }
      return next;
    });
  }, []);

  const handleSpeakerChange = useCallback(
    (id: string, speaker: string) => {
      const current = drafts.get(id);
      setDraft(id, {
        speaker,
        text: current?.text ?? segments.find((s) => s.id === id)?.text ?? "",
      });
    },
    [drafts, segments, setDraft],
  );

  const handleTextChange = useCallback(
    (id: string, text: string) => {
      const current = drafts.get(id);
      setDraft(id, {
        speaker:
          current?.speaker ?? segments.find((s) => s.id === id)?.speaker ?? "",
        text,
      });
    },
    [drafts, segments, setDraft],
  );

  const handleCancelEdit = useCallback(
    (id: string) => {
      setDraft(id, null);
    },
    [setDraft],
  );

  const handleMoveFocus = useCallback((from: string, direction: 1 | -1) => {
    const order = segmentOrder.current;
    const idx = order.indexOf(from);
    const nextIdx = idx + direction;
    const next = order[nextIdx];
    if (next === undefined) return;
    const target = textareaRefs.current[nextIdx];
    target?.focus();
  }, []);

  const saveAll = useCallback(async () => {
    const changes = segments.flatMap((seg) => {
      const d = drafts.get(seg.id);
      if (!d) return [];
      const out: { id: string; speaker?: string; text?: string } = {
        id: seg.id,
      };
      if (d.speaker !== seg.speaker) out.speaker = d.speaker;
      if (d.text !== seg.text) out.text = d.text;
      return out.speaker || out.text ? [out] : [];
    });
    if (changes.length === 0) {
      setEditing(false);
      setSaveStatus("saved");
      window.setTimeout(() => setSaveStatus("idle"), 2500);
      return;
    }
    setSaveStatus("saving");
    try {
      const result = await patchTranscript(itemId, latestVersion, changes);
      setLatestVersion(result.version);
      setDrafts(new Map());
      setSegments((prev) =>
        prev.map((seg) => {
          const d = drafts.get(seg.id);
          if (!d) return seg;
          return { ...seg, speaker: d.speaker, text: d.text };
        }),
      );
      setEditing(false);
      setSaveStatus("saved");
      window.setTimeout(() => setSaveStatus("idle"), 2500);
    } catch (e) {
      const conflict =
        e instanceof Error &&
        "status" in e &&
        (e as { status: number }).status === 409;
      setSaveStatus(conflict ? "conflict" : "error");
    }
  }, [itemId, latestVersion, segments, drafts]);

  const handleReload = useCallback(() => {
    setDrafts(new Map());
    setEditing(false);
    setSaveStatus("idle");
    onReload();
  }, [onReload]);

  const autoGrow = (el: HTMLTextAreaElement | null) => {
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
    textareaRefs.current[
      segmentOrder.current.indexOf(el?.dataset?.segmentId ?? "")
    ] = el;
  };

  return (
    <div className="transcript" ref={containerRef} onScroll={handleScroll}>
      <div className="transcript-toolbar">
        {follow && suspended && (
          <button
            type="button"
            className="transcript-resume-follow"
            onClick={handleResume}
          >
            继续跟随
          </button>
        )}
        {saveStatus === "saved" && (
          <span className="transcript-saved-hint">已保存</span>
        )}
        {saveStatus === "conflict" && (
          <span className="transcript-conflict-hint">
            转录已被其他进程修改
            <button
              type="button"
              className="transcript-reload-link"
              onClick={handleReload}
            >
              重新加载
            </button>
          </span>
        )}
        {saveStatus === "error" && (
          <span className="transcript-conflict-hint">保存失败，请重试</span>
        )}
        <span className="transcript-toolbar-spacer" />
        {editing ? (
          <>
            <button
              type="button"
              className="transcript-toolbar-button transcript-toolbar-button-primary"
              onClick={() => void saveAll()}
              disabled={saveStatus === "saving"}
            >
              {saveStatus === "saving" ? "保存中…" : "保存修改"}
            </button>
            <button
              type="button"
              className="transcript-toolbar-button"
              onClick={() => {
                setEditing(false);
                setDrafts(new Map());
              }}
            >
              取消
            </button>
          </>
        ) : (
          <button
            type="button"
            className="transcript-toolbar-button"
            onClick={() => {
              setDrafts(new Map());
              setEditing(true);
            }}
          >
            编辑转录
          </button>
        )}
      </div>
      {segments.map((segment) => {
        const idx = segmentOrder.current.indexOf(segment.id);
        return (
          <TranscriptSegment
            key={segment.id}
            segment={segment}
            active={segment.id === activeSegmentId}
            editing={editing}
            draft={drafts.get(segment.id) ?? null}
            textareaRef={(el) => {
              if (el) {
                el.dataset.segmentId = segment.id;
                autoGrow(el);
              } else {
                textareaRefs.current[idx] = null;
              }
            }}
            onSpeakerChange={handleSpeakerChange}
            onTextChange={handleTextChange}
            onCancelEdit={handleCancelEdit}
            onMoveFocus={handleMoveFocus}
            onSaveAll={() => void saveAll()}
            onTimestampClick={handleTimestampClick}
          />
        );
      })}
    </div>
  );
}
