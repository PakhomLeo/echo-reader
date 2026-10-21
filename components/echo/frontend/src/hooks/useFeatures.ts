import { useEffect, useState } from "react";
import { getFeatures, type Features } from "../api/enrichment";

/**
 * Optional features (TTS, OCR). `null` while loading or when the import service is not
 * reachable — callers treat that as "not available" and hide the entry points.
 */
export function useFeatures(): Features | null {
  const [features, setFeatures] = useState<Features | null>(null);
  useEffect(() => {
    let current = true;
    getFeatures()
      .then((value) => {
        if (current) setFeatures(value);
      })
      .catch(() => {
        if (current) setFeatures(null);
      });
    return () => {
      current = false;
    };
  }, []);
  return features;
}
