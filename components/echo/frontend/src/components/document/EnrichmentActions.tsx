import { useEffect, useRef, useState } from "react";
import { AudioLines, ScanText } from "lucide-react";
import {
  getEnrichmentJob,
  startEnrichment,
  type FeatureName,
  type Features,
} from "../../api/enrichment";

interface Props {
  itemId: string;
  features: Features | null;
  hasArticle: boolean;
  hasImages: boolean;
  /** Called after a job wrote new files (new audio attachment or ocr.json). */
  onCompleted: (feature: FeatureName) => void;
  /** Job polling interval (ms). */
  pollMs?: number;
}

const LABELS: Record<FeatureName, { idle: string; busy: string; done: string }> = {
  tts: { idle: "生成朗读音频", busy: "正在生成朗读…", done: "朗读音频已生成" },
  ocr: { idle: "识别图片文字", busy: "正在识别图片…", done: "图片文字已识别" },
};

/**
 * Entry points for the optional TTS / OCR features. A feature that is not installed or
 * not configured is not rendered at all, so the reader works the same without it.
 */
export function EnrichmentActions({
  itemId,
  features,
  hasArticle,
  hasImages,
  onCompleted,
  pollMs = 1500,
}: Props) {
  const [busy, setBusy] = useState<FeatureName | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);
  useEffect(() => {
    setBusy(null);
    setMessage(null);
    setFailed(false);
  }, [itemId]);

  const run = async (feature: FeatureName) => {
    setBusy(feature);
    setFailed(false);
    setMessage(LABELS[feature].busy);
    try {
      let job = await startEnrichment(feature, itemId);
      while (job.status === "queued" || job.status === "running") {
        await new Promise<void>((resolve) => {
          timer.current = window.setTimeout(resolve, pollMs);
        });
        job = await getEnrichmentJob(job.id);
      }
      if (job.status === "succeeded") {
        setMessage(LABELS[feature].done);
        onCompleted(feature);
      } else {
        setFailed(true);
        setMessage(job.error ?? "处理失败");
      }
    } catch (error) {
      setFailed(true);
      setMessage(error instanceof Error ? error.message : "处理失败");
    } finally {
      setBusy(null);
    }
  };

  const showTts = Boolean(features?.tts.available) && hasArticle;
  const showOcr = Boolean(features?.ocr.available) && hasImages;
  if (!showTts && !showOcr) return null;
  return (
    <span className="document-enrichment">
      {showTts && (
        <button type="button" onClick={() => void run("tts")} disabled={busy !== null}>
          <AudioLines size={15} />
          {LABELS.tts.idle}
        </button>
      )}
      {showOcr && (
        <button type="button" onClick={() => void run("ocr")} disabled={busy !== null}>
          <ScanText size={15} />
          {LABELS.ocr.idle}
        </button>
      )}
      {message && (
        <span className={failed ? "document-enrichment-error" : "document-enrichment-status"} role="status">
          {message}
        </span>
      )}
    </span>
  );
}
