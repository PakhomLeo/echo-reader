use crate::models::content::{Block, ContentJson};

pub fn content_to_markdown(content: &ContentJson) -> Result<String, String> {
    content.validate()?;
    let mut out = String::new();
    for block in &content.blocks {
        let md = match block {
            Block::Heading { level, text } => format!("{} {}", "#".repeat(*level as usize), text),
            Block::Blockquote { markdown } => markdown
                .lines()
                .map(|line| format!("> {line}"))
                .collect::<Vec<_>>()
                .join("\n"),
            Block::Paragraph { markdown }
            | Block::UnorderedList { markdown }
            | Block::OrderedList { markdown }
            | Block::Code { markdown } => markdown.clone(),
            Block::Image { path, alt, caption } => {
                let alt_text = alt.clone().unwrap_or_default();
                match caption {
                    Some(c) if !c.is_empty() => format!("![{alt_text}]({path} \"{c}\")"),
                    _ => format!("![{alt_text}]({path})"),
                }
            }
            Block::HorizontalRule => "---".into(),
        };
        out.push_str(&md);
        out.push_str("\n\n");
    }
    Ok(out.trim_end().to_string() + "\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::content::{Block, ContentJson};

    fn content(blocks: Vec<Block>) -> ContentJson {
        ContentJson {
            schema_version: 1,
            blocks,
        }
    }

    #[test]
    fn renders_heading_paragraph_blockquote() {
        let c = content(vec![
            Block::Heading {
                level: 1,
                text: "标题".into(),
            },
            Block::Paragraph {
                markdown: "第一段 **粗体**".into(),
            },
            Block::Blockquote {
                markdown: "引用内容".into(),
            },
        ]);
        let md = content_to_markdown(&c).unwrap();
        assert_eq!(md, "# 标题\n\n第一段 **粗体**\n\n> 引用内容\n");
    }

    #[test]
    fn image_has_blank_lines_around() {
        let c = content(vec![
            Block::Paragraph {
                markdown: "文字A".into(),
            },
            Block::Image {
                path: "images/1.webp".into(),
                alt: Some("图".into()),
                caption: Some("说明".into()),
            },
            Block::Paragraph {
                markdown: "文字B".into(),
            },
        ]);
        let md = content_to_markdown(&c).unwrap();
        assert!(md.contains("文字A\n\n![图](images/1.webp \"说明\")\n\n文字B"));
    }

    #[test]
    fn image_without_caption_omits_title() {
        let c = content(vec![Block::Image {
            path: "images/2.png".into(),
            alt: Some("图".into()),
            caption: None,
        }]);
        let md = content_to_markdown(&c).unwrap();
        assert!(md.contains("![图](images/2.png)"));
        assert!(!md.contains("\""));
    }

    #[test]
    fn preserves_image_order() {
        let c = content(vec![
            Block::Image {
                path: "images/a.png".into(),
                alt: None,
                caption: None,
            },
            Block::Image {
                path: "images/b.png".into(),
                alt: None,
                caption: None,
            },
        ]);
        let md = content_to_markdown(&c).unwrap();
        let pos_a = md.find("images/a.png").unwrap();
        let pos_b = md.find("images/b.png").unwrap();
        assert!(pos_a < pos_b);
    }

    #[test]
    fn renders_lists_code_rule() {
        let c = content(vec![
            Block::UnorderedList {
                markdown: "- a\n- b".into(),
            },
            Block::OrderedList {
                markdown: "1. x".into(),
            },
            Block::Code {
                markdown: "fn main() {}".into(),
            },
            Block::HorizontalRule,
        ]);
        let md = content_to_markdown(&c).unwrap();
        assert!(md.contains("- a\n- b"));
        assert!(md.contains("1. x"));
        assert!(md.contains("fn main() {}"));
        assert!(md.contains("---"));
    }

    #[test]
    fn rejects_bad_heading_level() {
        let c = content(vec![Block::Heading {
            level: 7,
            text: "x".into(),
        }]);
        assert!(content_to_markdown(&c).is_err());
    }
}
