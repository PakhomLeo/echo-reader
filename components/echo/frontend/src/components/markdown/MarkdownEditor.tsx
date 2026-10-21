import { useCallback, useEffect, useRef, useState } from "react";
import { uploadImage } from "../../api/items";
import { MarkdownRenderer } from "./MarkdownRenderer";
import "./MarkdownEditor.css";

interface MarkdownEditorProps {
  itemId: string;
  initialValue: string;
  onSave: (markdown: string) => Promise<void>;
  onCancel: () => void;
}

type EditorStatus = "loading" | "ready" | "error";

interface EditorHandle {
  getValue: () => string;
  insertAtCursor: (text: string) => void;
  destroy: () => void;
}

export function MarkdownEditor({
  itemId,
  initialValue,
  onSave,
  onCancel,
}: MarkdownEditorProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorHandle | null>(null);
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;
  const [status, setStatus] = useState<EditorStatus>("loading");
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState(initialValue);

  // 动态加载 CodeMirror（不在首包）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mod = await import("./codemirrorSetup");
        if (cancelled) return;
        const host = hostRef.current;
        if (!host) return;
        viewRef.current = mod.createEditor(host, initialValue, {
          onPasteImage: (file) => {
            void handlePasteImage(file);
          },
        });
        setStatus("ready");
      } catch {
        if (!cancelled) setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
      viewRef.current?.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialValue]);

  const handlePasteImage = useCallback(
    async (file: File) => {
      setError(null);
      try {
        const result = await uploadImage(itemId, file);
        const view = viewRef.current;
        if (!view) return;
        view.insertAtCursor(`${result.markdown}\n\n`);
      } catch (e) {
        setError(
          e instanceof Error ? `图片上传失败：${e.message}` : "图片上传失败",
        );
      }
    },
    [itemId],
  );

  const handleSave = async () => {
    const view = viewRef.current;
    if (!view || saving) return;
    const markdown = view.getValue();
    setSaving(true);
    setError(null);
    try {
      await onSaveRef.current(markdown);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handlePreview = () => {
    const view = viewRef.current;
    if (!view) return;
    setDraft(view.getValue());
    setPreview((v) => !v);
  };

  const handleCancel = () => {
    const view = viewRef.current;
    if (!view) return;
    if (view.getValue() !== initialValue) {
      const ok = window.confirm("放弃未保存的修改？");
      if (!ok) return;
    }
    onCancel();
  };

  return (
    <div className="md-editor">
      <div className="md-editor-toolbar">
        <span className="md-editor-title">编辑文章</span>
        <span className="md-editor-actions">
          {preview ? (
            <button
              type="button"
              className="md-editor-button"
              onClick={handlePreview}
            >
              返回编辑
            </button>
          ) : (
            <button
              type="button"
              className="md-editor-button"
              onClick={handlePreview}
              disabled={status !== "ready"}
            >
              预览
            </button>
          )}
          <button
            type="button"
            className="md-editor-button md-editor-button-primary"
            onClick={handleSave}
            disabled={saving || status !== "ready"}
          >
            {saving ? "保存中…" : "保存"}
          </button>
          <button
            type="button"
            className="md-editor-button"
            onClick={handleCancel}
            disabled={saving}
          >
            取消
          </button>
        </span>
      </div>
      {error && <div className="md-editor-error">{error}</div>}
      {status === "loading" && (
        <div className="md-editor-placeholder">正在加载编辑器…</div>
      )}
      {status === "error" && (
        <div className="md-editor-error">编辑器加载失败，请刷新页面重试</div>
      )}
      {!preview ? (
        <div className="md-editor-host" ref={hostRef} />
      ) : (
        <div className="md-editor-preview">
          <MarkdownRenderer itemId={itemId} markdown={draft} />
        </div>
      )}
    </div>
  );
}
