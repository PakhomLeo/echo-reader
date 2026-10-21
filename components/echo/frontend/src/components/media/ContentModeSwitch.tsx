export type ContentMode = "article" | "transcript";

interface ContentModeSwitchProps {
  hasArticle: boolean;
  hasTranscript: boolean;
  mode: ContentMode;
  onModeChange: (mode: ContentMode) => void;
}

export function ContentModeSwitch({
  hasArticle,
  hasTranscript,
  mode,
  onModeChange,
}: ContentModeSwitchProps) {
  if (!hasArticle || !hasTranscript) return null;

  return (
    <div className="content-mode-switch" role="tablist" aria-label="内容模式">
      <button
        type="button"
        role="tab"
        aria-selected={mode === "article"}
        className={`content-mode-button${mode === "article" ? " content-mode-button-active" : ""}`}
        onClick={() => onModeChange("article")}
      >
        文章
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={mode === "transcript"}
        className={`content-mode-button${mode === "transcript" ? " content-mode-button-active" : ""}`}
        onClick={() => onModeChange("transcript")}
      >
        时间线文本
      </button>
    </div>
  );
}
