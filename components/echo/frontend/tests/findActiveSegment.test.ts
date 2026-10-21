import { describe, expect, it } from "vitest";
import { findActiveSegment } from "../src/components/transcript/findActiveSegment";
import type { TranscriptSegment } from "../src/api/types";

function seg(id: string, start_ms: number, end_ms: number): TranscriptSegment {
  return { id, start_ms, end_ms, speaker: "s", text: "t" };
}

const segments: TranscriptSegment[] = [
  seg("a", 0, 2000),
  seg("b", 2000, 5000),
  seg("c", 5000, 9000),
];

describe("findActiveSegment", () => {
  it("命中中间段", () => {
    expect(findActiveSegment(segments, 3000)?.id).toBe("b");
  });

  it("start_ms 是闭边界", () => {
    expect(findActiveSegment(segments, 2000)?.id).toBe("b");
  });

  it("end_ms 是开边界（等于 end 落入下一段）", () => {
    expect(findActiveSegment(segments, 5000)?.id).toBe("c");
  });

  it("命中第一段", () => {
    expect(findActiveSegment(segments, 0)?.id).toBe("a");
  });

  it("命中最后一段的末尾", () => {
    expect(findActiveSegment(segments, 8999)?.id).toBe("c");
  });

  it("在段间间隙返回 null", () => {
    expect(findActiveSegment(segments, 9000)).toBeNull();
    expect(findActiveSegment(segments, -1)).toBeNull();
  });

  it("空列表返回 null", () => {
    expect(findActiveSegment([], 100)).toBeNull();
  });
});
