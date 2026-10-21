import { useCallback, useEffect, useState } from "react";
import { browseDirectory, type BrowseResponse } from "../../api/items";
import "./FolderPickerModal.css";

interface FolderPickerModalProps {
  onClose: () => void;
  onSelect: (path: string) => Promise<void>;
}

export function FolderPickerModal({
  onClose,
  onSelect,
}: FolderPickerModalProps) {
  const [current, setCurrent] = useState<BrowseResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectError, setSelectError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState(false);

  const joinPath = useCallback((path: string, name: string) => {
    if (!path) return name;
    return path.endsWith("\\") ? `${path}${name}` : `${path}\\${name}`;
  }, []);

  const navigate = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    setSelectError(null);
    try {
      setCurrent(await browseDirectory(path));
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法读取该文件夹");
    } finally {
      setLoading(false);
    }
  }, []);

  // 打开时先列出磁盘根
  useEffect(() => {
    void navigate("");
  }, [navigate]);

  const handleSelect = async () => {
    if (!current || current.path === "") return;
    setSelecting(true);
    setSelectError(null);
    try {
      await onSelect(current.path);
    } catch (e) {
      setSelectError(e instanceof Error ? e.message : "打开文件夹失败");
    } finally {
      setSelecting(false);
    }
  };

  return (
    <div
      className="picker-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="选择文件夹"
      aria-busy={selecting}
    >
      <div className="picker-panel">
        <div className="picker-header">
          <span className="picker-title">选择文件夹</span>
          <button
            type="button"
            className="picker-close"
            onClick={onClose}
            disabled={selecting}
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        <div className="picker-pathbar">
          <button
            type="button"
            className="picker-parent"
            disabled={!current?.parent || selecting}
            onClick={() => {
              if (current?.parent) void navigate(current.parent);
            }}
            title="上一级"
          >
            ↑ 上一级
          </button>
          <span className="picker-path" title={current?.path ?? ""}>
            {current?.path || "本机磁盘"}
          </span>
        </div>

        <div className="picker-list">
          {loading ? (
            <div className="picker-hint">加载中…</div>
          ) : error ? (
            <div className="picker-error">{error}</div>
          ) : (current?.entries.length ?? 0) === 0 ? (
            <div className="picker-hint">该文件夹为空</div>
          ) : (
            current?.entries.map((entry) =>
              entry.is_dir ? (
                <button
                  key={entry.name}
                  type="button"
                  className="picker-row"
                  disabled={selecting}
                  onClick={() =>
                    void navigate(joinPath(current.path, entry.name))
                  }
                >
                  <span className="picker-row-icon" aria-hidden>
                    📁
                  </span>
                  <span className="picker-row-name">{entry.name}</span>
                </button>
              ) : (
                <div key={entry.name} className="picker-row picker-row-file">
                  <span className="picker-row-icon" aria-hidden>
                    📄
                  </span>
                  <span className="picker-row-name">{entry.name}</span>
                </div>
              ),
            )
          )}
        </div>

        <div className="picker-footer">
          {selectError && (
            <span className="picker-error-inline">{selectError}</span>
          )}
          <span className="picker-footer-spacer" />
          <button
            type="button"
            className="picker-button"
            onClick={onClose}
            disabled={selecting}
          >
            取消
          </button>
          <button
            type="button"
            className="picker-button picker-button-primary"
            onClick={() => void handleSelect()}
            disabled={!current || current.path === "" || selecting}
          >
            {selecting ? "打开中…" : "选择此文件夹"}
          </button>
        </div>
      </div>
    </div>
  );
}
