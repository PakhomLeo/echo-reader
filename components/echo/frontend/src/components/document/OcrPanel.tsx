import { useEffect, useState } from "react";
import { getItemOcr, type OcrSidecar } from "../../api/enrichment";

/** Shows `ocr.json` results when the item has them; renders nothing otherwise. */
export function OcrPanel({ itemId, version }: { itemId: string; version: number }) {
  const [data, setData] = useState<OcrSidecar | null>(null);
  useEffect(() => {
    let current = true;
    getItemOcr(itemId)
      .then((value) => {
        if (current) setData(value);
      })
      .catch(() => {
        if (current) setData(null);
      });
    return () => {
      current = false;
    };
  }, [itemId, version]);

  const images = data?.images.filter((image) => image.text.trim()) ?? [];
  if (images.length === 0) return null;
  return (
    <details className="document-ocr">
      <summary>图片文字（{images.length} 张，{data?.engine}）</summary>
      <ol>
        {images.map((image) => (
          <li key={image.path}>
            <code>{image.path}</code>
            <p>{image.text}</p>
          </li>
        ))}
      </ol>
    </details>
  );
}
