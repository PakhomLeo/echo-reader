use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TranscriptJson {
    pub schema_version: u32,
    pub language: String,
    pub media_id: String,
    pub segments: Vec<Segment>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Segment {
    pub id: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub speaker: String,
    pub text: String,
}

impl TranscriptJson {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != 1 {
            return Err(format!("不支持的 schema_version: {}", self.schema_version));
        }
        let mut seen: std::collections::HashSet<&str> = Default::default();
        let mut prev_end: Option<u64> = None;
        for seg in &self.segments {
            if seg.id.is_empty() {
                return Err("segment.id 为空".into());
            }
            if !seen.insert(&seg.id) {
                return Err(format!("重复的 segment id: {}", seg.id));
            }
            if seg.end_ms <= seg.start_ms {
                return Err(format!(
                    "segment {} 时间区间非法: [{}, {})",
                    seg.id, seg.start_ms, seg.end_ms
                ));
            }
            if let Some(prev) = prev_end
                && seg.start_ms < prev
            {
                return Err(format!("segment {} 未按 start_ms 升序排列", seg.id));
            }
            prev_end = Some(seg.start_ms);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seg(id: &str, start: u64, end: u64) -> Segment {
        Segment {
            id: id.into(),
            start_ms: start,
            end_ms: end,
            speaker: "s".into(),
            text: "t".into(),
        }
    }

    fn transcript(segments: Vec<Segment>) -> TranscriptJson {
        TranscriptJson {
            schema_version: 1,
            language: "zh-CN".into(),
            media_id: "primary".into(),
            segments,
        }
    }

    #[test]
    fn valid_sequence_passes() {
        let t = transcript(vec![
            seg("a", 0, 1000),
            seg("b", 1000, 2000),
            seg("c", 2500, 3000),
        ]);
        assert!(t.validate().is_ok());
    }

    #[test]
    fn overlap_allowed_slightly() {
        let t = transcript(vec![seg("a", 0, 1000), seg("b", 990, 2000)]);
        assert!(t.validate().is_ok());
    }

    #[test]
    fn unsorted_rejected() {
        let t = transcript(vec![seg("a", 5000, 6000), seg("b", 0, 1000)]);
        assert!(t.validate().is_err());
    }

    #[test]
    fn end_not_after_start_rejected() {
        let t = transcript(vec![seg("a", 1000, 1000)]);
        assert!(t.validate().is_err());
    }

    #[test]
    fn duplicate_id_rejected() {
        let t = transcript(vec![seg("a", 0, 1000), seg("a", 2000, 3000)]);
        assert!(t.validate().is_err());
    }

    #[test]
    fn empty_id_rejected() {
        let t = transcript(vec![seg("", 0, 1000)]);
        assert!(t.validate().is_err());
    }
}
