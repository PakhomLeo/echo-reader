//! Turn the files of one library item into searchable chunks.

use pulldown_cmark::{Event, Parser, Tag, TagEnd};
use serde::Deserialize;

use super::index::{DocSource, OCR_SIDECAR};
use crate::models::transcript::TranscriptJson;

#[derive(Debug, Clone, PartialEq)]
pub struct Chunk {
    pub kind: &'static str,
    pub locator: String,
    pub start_ms: Option<i64>,
    pub body: String,
}

/// Optional OCR sidecar written by the import service (`ocr.json` in the item folder).
#[derive(Debug, Deserialize)]
pub struct OcrSidecar {
    #[serde(default)]
    pub images: Vec<OcrImage>,
}

#[derive(Debug, Deserialize)]
pub struct OcrImage {
    pub path: String,
    #[serde(default)]
    pub text: String,
}

const MAX_BLOCK_CHARS: usize = 1200;

pub fn extract_chunks(source: &DocSource) -> Vec<Chunk> {
    let mut chunks = vec![Chunk {
        kind: "title",
        locator: String::new(),
        start_ms: None,
        body: match &source.author {
            Some(author) if !author.trim().is_empty() => format!("{} {}", source.title, author),
            _ => source.title.clone(),
        },
    }];
    if let Some(path) = &source.article_path
        && let Ok(markdown) = std::fs::read_to_string(path)
    {
        for (n, block) in markdown_blocks(&markdown).into_iter().enumerate() {
            chunks.push(Chunk {
                kind: "article",
                locator: n.to_string(),
                start_ms: None,
                body: block,
            });
        }
    }
    if let Some(path) = &source.transcript_path
        && let Ok(bytes) = std::fs::read(path)
        && let Ok(transcript) = serde_json::from_slice::<TranscriptJson>(&bytes)
    {
        for segment in transcript.segments {
            let text = segment.text.trim();
            if text.is_empty() {
                continue;
            }
            chunks.push(Chunk {
                kind: "transcript",
                locator: segment.id.clone(),
                start_ms: Some(segment.start_ms as i64),
                body: text.to_owned(),
            });
        }
    }
    if let Ok(bytes) = std::fs::read(source.folder_path.join(OCR_SIDECAR))
        && let Ok(ocr) = serde_json::from_slice::<OcrSidecar>(&bytes)
    {
        for image in ocr.images {
            let text = image.text.trim();
            if text.is_empty() {
                continue;
            }
            chunks.push(Chunk {
                kind: "ocr",
                locator: image.path.clone(),
                start_ms: None,
                body: text.to_owned(),
            });
        }
    }
    chunks
}

/// Plain-text blocks (paragraphs, headings, list items, quotes, code) of a Markdown file.
/// Image alt text and link text are kept; URLs are dropped.
pub fn markdown_blocks(markdown: &str) -> Vec<String> {
    let mut blocks = Vec::new();
    let mut current = String::new();
    let flush = |current: &mut String, blocks: &mut Vec<String>| {
        let text = current.split_whitespace().collect::<Vec<_>>().join(" ");
        current.clear();
        if text.is_empty() {
            return;
        }
        let chars: Vec<char> = text.chars().collect();
        for part in chars.chunks(MAX_BLOCK_CHARS) {
            blocks.push(part.iter().collect());
        }
    };
    for event in Parser::new(markdown) {
        match event {
            Event::Text(text) | Event::Code(text) => current.push_str(&text),
            Event::SoftBreak | Event::HardBreak => current.push(' '),
            Event::Start(Tag::Paragraph | Tag::Heading { .. } | Tag::Item | Tag::CodeBlock(_))
            | Event::End(
                TagEnd::Paragraph
                | TagEnd::Heading(_)
                | TagEnd::Item
                | TagEnd::CodeBlock
                | TagEnd::TableRow,
            ) => flush(&mut current, &mut blocks),
            Event::End(TagEnd::TableCell) => current.push(' '),
            _ => {}
        }
    }
    flush(&mut current, &mut blocks);
    blocks
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_keep_text_and_alt_but_drop_urls() {
        let blocks = markdown_blocks(
            "# 标题\n\n第一段 [链接](https://example.com/x) **加粗**\n\n![图片说明](images/1.webp)\n\n- 列表项\n",
        );
        assert_eq!(
            blocks,
            vec!["标题", "第一段 链接 加粗", "图片说明", "列表项"]
        );
    }
}
