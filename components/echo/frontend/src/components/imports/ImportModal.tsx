import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, X } from "lucide-react";
import type {
  AsrBackend,
  CreateImportBatchRequest,
  ImportCapabilities,
  ImportMode,
  LocalTimestampMode,
  RequestedAsrBackend,
} from "../../api/imports";
import { MAX_IMPORT_URLS, parseImportUrls } from "./parseImportUrls";
import "./ImportModal.css";

interface ImportModalProps {
  capabilities: ImportCapabilities | null;
  online: boolean | null;
  submitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (request: CreateImportBatchRequest) => Promise<unknown>;
  onViewTasks: () => void;
}

const MODES: { value: ImportMode; title: string; description: string }[] = [
  { value: "auto", title: "智能识别", description: "按链接内容自动选择提取方式" },
  { value: "download", title: "保存媒体", description: "保留媒体与原始信息" },
  { value: "transcribe", title: "转写", description: "生成带时间线的原始转写" },
];

const BACKEND_LABEL: Record<AsrBackend, string> = {
  online: "在线识别",
  local: "本地识别",
};

export function ImportModal(props: ImportModalProps) {
  const { onClose } = props;
  const dialogRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<ImportMode>("auto");
  const [backend, setBackend] = useState<RequestedAsrBackend>("auto");
  const [backendTouched, setBackendTouched] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [audioFormat, setAudioFormat] = useState("mp3");
  const [speedFactor, setSpeedFactor] = useState(1);
  const [timeout, setTimeoutValue] = useState(1800);
  const [timestampMode, setTimestampMode] = useState<LocalTimestampMode>("vad");
  const [diarization, setDiarization] = useState(true);
  const [speakerCount, setSpeakerCount] = useState(0);
  const [submitted, setSubmitted] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const parsed = useMemo(() => parseImportUrls(text), [text]);
  const limit = props.capabilities?.batch_max_urls ?? MAX_IMPORT_URLS;
  const errors = useMemo(() => {
    const next = [...parsed.errors];
    if (parsed.urls.length > limit && !next.some((value) => value.includes("一次最多"))) {
      next.push(`一次最多导入 ${limit} 个不同链接，当前为 ${parsed.urls.length} 个`);
    }
    return next;
  }, [limit, parsed]);
  const formErrors = fileError ? [fileError] : errors;

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    inputRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href]',
        ),
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previous?.focus();
    };
  }, [onClose]);

  // Auto remains server-selected. Capabilities only annotate it; they never replace a touched choice.
  useEffect(() => {
    if (!backendTouched) setBackend("auto");
  }, [backendTouched, props.capabilities]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitted(false);
    if (formErrors.length || parsed.urls.length === 0) return;
    try {
      await props.onSubmit({
        urls: parsed.urls,
        mode,
        audio: null,
        audio_format: audioFormat,
        speed_factor: backend === "local" ? 1 : speedFactor,
        timeout: backend === "local" ? null : timeout,
        asr_backend: backend,
        local_timestamp_mode: timestampMode,
        local_diarization: diarization,
        local_speaker_count: diarization ? speakerCount : 0,
      });
      setSubmitted(true);
      setText("");
    } catch {
      // The controller exposes the service error through props.error.
    }
  }

  async function loadFile(file: File | undefined) {
    if (!file) return;
    setFileError(null);
    try {
      const contents = await file.text();
      setText((current) => [current.trimEnd(), contents].filter(Boolean).join("\n"));
    } catch {
      setFileError("无法读取所选文件，请重新选择 .txt 或 .csv 文件");
    }
  }

  return (
    <div className="import-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && props.onClose()}>
      <section ref={dialogRef} className="import-modal" role="dialog" aria-modal="true" aria-labelledby="import-title">
        <header className="import-header">
          <div><p className="import-eyebrow">添加到 Echo</p><h2 id="import-title">批量添加链接</h2></div>
          <button className="icon-button" type="button" onClick={props.onClose} aria-label="关闭添加内容"><X size={19} /></button>
        </header>

        {props.online === false && <div className="import-offline" role="status"><AlertCircle size={18} /><div><strong>本地导入服务未启动</strong><p>启动 Echo Reader 后重试。</p></div></div>}

        <form className="import-form" onSubmit={(event) => void submit(event)} noValidate>
          <label className="field-label" htmlFor="import-urls">内容链接（每行一个）</label>
          <textarea ref={inputRef} id="import-urls" rows={6} value={text} onChange={(e) => { setFileError(null); setText(e.target.value); }} placeholder={"https://example.com/article\nhttps://example.com/video?list=1"} aria-describedby="import-url-summary import-url-errors" aria-invalid={formErrors.length > 0 && (text.length > 0 || fileError !== null)} />
          <div className="import-input-meta">
            <label className="file-button">从文件添加<input type="file" accept=".txt,.csv,text/plain,text/csv" onChange={(e) => void loadFile(e.target.files?.[0])} /></label>
            <span id="import-url-summary" aria-live="polite">{parsed.inputCount} 行 · {parsed.urls.length} 个有效链接{parsed.duplicateCount ? ` · ${parsed.duplicateCount} 个重复已忽略` : ""} · 上限 {limit}</span>
          </div>
          <div id="import-url-errors" aria-live="assertive">{(text || fileError) && formErrors.map((message) => <p className="import-error" role="alert" key={message}>{message}</p>)}</div>

          <fieldset className="import-modes"><legend>处理方式</legend>{MODES.map((entry) => <label key={entry.value} className={mode === entry.value ? "import-mode import-mode-selected" : "import-mode"}><input type="radio" name="mode" value={entry.value} checked={mode === entry.value} onChange={() => setMode(entry.value)} /><span><strong>{entry.title}</strong><small>{entry.description}</small></span></label>)}</fieldset>
          <p className="import-explanation">本次转写会生成原始时间线文本。只有另行配置在线精炼能力后，系统才可生成 AI 精炼文章。</p>

          <fieldset className="import-backends"><legend>识别后端</legend>
            <label className={backend === "auto" ? "import-backend import-backend-selected" : "import-backend"}><input type="radio" name="backend" checked={backend === "auto"} onChange={() => { setBackendTouched(true); setBackend("auto"); }} /><span><strong>智能选择</strong><small>{props.capabilities?.recommended_backend ? `服务推荐：${BACKEND_LABEL[props.capabilities.recommended_backend]}` : "由服务按可用性选择"}</small></span></label>
            {(["online", "local"] as const).map((id) => {
              const capability = props.capabilities?.backends[id];
              const disabled = capability?.available === false;
              return <label key={id} className={backend === id ? "import-backend import-backend-selected" : "import-backend"}><input type="radio" name="backend" checked={backend === id} disabled={disabled} onChange={() => { setBackendTouched(true); setBackend(id); }} /><span><strong>{BACKEND_LABEL[id]}</strong><small>{disabled ? capability.reason || "当前不可用" : capability?.readiness === "download_required" ? "首次使用需要下载模型" : "可用"}</small></span></label>;
            })}
          </fieldset>

          <div className="import-advanced"><button type="button" onClick={() => setAdvanced((value) => !value)} aria-expanded={advanced}>高级选项</button>{advanced && <div className="advanced-grid">
            <label>音频格式<select value={audioFormat} onChange={(e) => setAudioFormat(e.target.value)}><option value="mp3">MP3</option><option value="m4a">M4A</option><option value="wav">WAV</option></select></label>
            {backend !== "local" && <><label>速度系数<input type="number" min="0.5" max="4" step="0.1" value={speedFactor} onChange={(e) => setSpeedFactor(Number(e.target.value))} /></label><label>超时（秒）<input type="number" min="60" max="7200" value={timeout} onChange={(e) => setTimeoutValue(Number(e.target.value))} /></label></>}
            <fieldset className="local-timestamp-options"><legend>时间线来源</legend><label><input type="radio" name="timestamp" checked={timestampMode === "model"} onChange={() => setTimestampMode("model")} />模型原生</label><label><input type="radio" name="timestamp" checked={timestampMode === "vad"} onChange={() => setTimestampMode("vad")} />VAD 切段</label></fieldset>
            <label className="local-checkbox"><input type="checkbox" checked={diarization} onChange={(e) => setDiarization(e.target.checked)} />说话人分离（匿名）</label>
            <label>说话人数（0 为自动）<input type="number" min="0" max="20" value={speakerCount} disabled={!diarization} onChange={(e) => setSpeakerCount(Math.min(20, Math.max(0, Number(e.target.value) || 0)))} /></label>
          </div>}</div>

          {submitted && <p className="import-submit-success" role="status" aria-live="polite"><CheckCircle2 size={16} />批次已提交。<button type="button" className="text-button" onClick={props.onViewTasks}>查看任务</button></p>}
          {props.error && props.online !== false && <p className="import-error" role="alert">{props.error}</p>}
          <button className="primary-button import-submit" disabled={props.online !== true || props.submitting || formErrors.length > 0 || parsed.urls.length === 0}>{props.submitting ? "提交中…" : `添加 ${parsed.urls.length || ""} 个链接`}</button>
        </form>
      </section>
    </div>
  );
}
