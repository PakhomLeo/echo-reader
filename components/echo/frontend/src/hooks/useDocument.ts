import { useEffect, useState } from "react";
import { getItem } from "../api/items";
import type { ItemDetail } from "../api/types";

export type DocumentState =
  | { status: "loading" }
  | { status: "ready"; detail: ItemDetail }
  | { status: "not_found" }
  | { status: "network_error"; message: string };

export function useDocument(itemId: string | undefined): DocumentState {
  const [state, setState] = useState<DocumentState>({ status: "loading" });

  useEffect(() => {
    if (!itemId) {
      setState({ status: "not_found" });
      return;
    }
    let cancelled = false;
    setState({ status: "loading" });
    getItem(itemId)
      .then((detail) => {
        if (!cancelled) setState({ status: "ready", detail });
      })
      .catch((e) => {
        if (cancelled) return;
        if (
          e instanceof Error &&
          "status" in e &&
          (e as { status: number }).status === 404
        ) {
          setState({ status: "not_found" });
        } else {
          setState({
            status: "network_error",
            message: e instanceof Error ? e.message : "加载失败",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [itemId]);

  return state;
}
