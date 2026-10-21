import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EnrichmentActions } from "../src/components/document/EnrichmentActions";
import { OcrPanel } from "../src/components/document/OcrPanel";
import type { Features } from "../src/api/enrichment";

const disabled: Features = {
  tts: { available: false, reason: "dependencies_missing", message: "朗读（Kokoro 语音合成）功能未启用：未安装可选依赖。" },
  ocr: { available: false, reason: "dependencies_missing", message: "图片文字识别（OCR）功能未启用：未安装可选依赖。" },
};
const enabled: Features = {
  tts: { available: true, reason: null, engine: "kokoro" },
  ocr: { available: true, reason: null, engine: "rapidocr" },
};

function json(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("EnrichmentActions", () => {
  it("renders nothing when the features are unavailable or unknown", () => {
    const { container, rerender } = render(
      <EnrichmentActions itemId="a" features={null} hasArticle hasImages onCompleted={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    rerender(<EnrichmentActions itemId="a" features={disabled} hasArticle hasImages onCompleted={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<EnrichmentActions itemId="a" features={enabled} hasArticle={false} hasImages={false} onCompleted={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("starts a job, polls it and reports completion", async () => {
    const statuses = ["running", "succeeded"];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        expect(url).toBe("/api/v1/items/a/tts");
        return json({ id: "j1", kind: "tts", item_id: "a", status: "queued", error: null, result: null }, { status: 202 });
      }
      return json({ id: "j1", kind: "tts", item_id: "a", status: statuses.shift(), error: null, result: {} });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onCompleted = vi.fn();
    render(<EnrichmentActions itemId="a" features={enabled} hasArticle hasImages={false} onCompleted={onCompleted} pollMs={1} />);
    expect(screen.queryByRole("button", { name: /识别图片文字/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /生成朗读音频/ }));
    await waitFor(() => expect(onCompleted).toHaveBeenCalledWith("tts"));
    expect(screen.getByRole("status")).toHaveTextContent("朗读音频已生成");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/enrichment-jobs/j1", undefined);
  });

  it("shows the feature_disabled message returned by the service", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(
      { detail: { code: "feature_disabled", feature: "ocr", message: "图片文字识别（OCR）功能未启用：未下载模型文件。" } },
      { status: 503 },
    )));
    render(<EnrichmentActions itemId="a" features={enabled} hasArticle={false} hasImages onCompleted={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /识别图片文字/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("功能未启用");
  });
});

describe("OcrPanel", () => {
  it("lists recognized text and hides when there is none", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({
      schema_version: 1,
      engine: "rapidocr",
      created_at: "2026-10-21T00:00:00Z",
      images: [
        { path: "images/1.png", text: "海报上的文字" },
        { path: "images/2.png", text: "", error: "bad" },
      ],
    })));
    render(<OcrPanel itemId="a" version={0} />);
    expect(await screen.findByText("海报上的文字")).toBeInTheDocument();
    expect(screen.getByText(/图片文字（1 张，rapidocr）/)).toBeInTheDocument();
  });

  it("renders nothing when the item has no ocr.json", async () => {
    const fetchMock = vi.fn(async () => json({ code: "NOT_FOUND", message: "x" }, { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<OcrPanel itemId="a" version={0} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
