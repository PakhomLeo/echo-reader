import type { TranscriptSegment } from "../../api/types";

/**
 * 二分查找当前播放时间对应的转录段。
 * 约束：segments 按 start_ms 升序；当前段满足 start_ms <= t < end_ms。
 */
export function findActiveSegment(
  segments: readonly TranscriptSegment[],
  currentMs: number,
): TranscriptSegment | null {
  let lo = 0;
  let hi = segments.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const seg = segments[mid];
    if (currentMs < seg.start_ms) {
      hi = mid - 1;
    } else if (currentMs >= seg.end_ms) {
      lo = mid + 1;
    } else {
      return seg;
    }
  }
  return null;
}
