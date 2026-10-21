use pulldown_cmark::{Event, Parser, Tag, TagEnd};

/// 收集 markdown 中 src 相同图片的出现位置（byte 范围，按出现顺序编号）。
fn image_ranges(markdown: &str, src: &str) -> Vec<(usize, usize)> {
    let mut ranges = Vec::new();
    let mut stack: Vec<(usize, String)> = Vec::new();
    for (event, range) in Parser::new(markdown).into_offset_iter() {
        match event {
            Event::Start(Tag::Image { dest_url, .. }) => {
                stack.push((range.start, dest_url.to_string()));
            }
            Event::End(TagEnd::Image) => {
                if let Some((start, url)) = stack.pop()
                    && url == src
                {
                    ranges.push((start, range.end));
                }
            }
            _ => {}
        }
    }
    ranges
}

/// 删除 markdown 中 src 匹配的第 occurrence 张图片（整个语法，连同所在行）。
/// 返回删除后的完整 markdown；不存在该 occurrence 时返回 None。
pub fn remove_image_occurrence(markdown: &str, src: &str, occurrence: usize) -> Option<String> {
    let ranges = image_ranges(markdown, src);
    let (start, end) = *ranges.get(occurrence)?;

    let bytes = markdown.as_bytes();
    let mut line_start = start;
    while line_start > 0 && bytes[line_start - 1] != b'\n' {
        line_start -= 1;
    }
    let mut line_end = end;
    while line_end < bytes.len() && bytes[line_end] != b'\n' {
        line_end += 1;
    }
    if line_end < bytes.len() {
        line_end += 1; // 吃掉行尾换行
    }

    let mut out = String::with_capacity(markdown.len() - (line_end - line_start));
    out.push_str(&markdown[..line_start]);
    out.push_str(&markdown[line_end..]);
    Some(out)
}

/// 检查 markdown 中是否仍引用给定 src 的图片。
pub fn references_image(markdown: &str, src: &str) -> bool {
    !image_ranges(markdown, src).is_empty()
}

#[cfg(test)]
mod tests {
    use super::*;

    const MD: &str = "第一段\n\n![图A](images/a.webp)\n\n中段 ![图B](images/b.png) 结尾\n\n![图A](images/a.webp)\n";

    #[test]
    fn removes_first_occurrence_only() {
        let out = remove_image_occurrence(MD, "images/a.webp", 0).unwrap();
        assert!(!out.contains("![图A](images/a.webp)\n\n中段"));
        assert!(out.contains("![图A](images/a.webp)\n"));
    }

    #[test]
    fn removes_second_occurrence_only() {
        let out = remove_image_occurrence(MD, "images/a.webp", 1).unwrap();
        assert!(out.contains("![图A](images/a.webp)\n\n中段"));
        assert!(!out.ends_with("![图A](images/a.webp)\n"));
    }

    #[test]
    fn missing_occurrence_returns_none() {
        assert!(remove_image_occurrence(MD, "images/a.webp", 5).is_none());
        assert!(remove_image_occurrence(MD, "images/nope.webp", 0).is_none());
    }

    #[test]
    fn removes_inline_image_with_line() {
        let out = remove_image_occurrence(MD, "images/b.png", 0).unwrap();
        assert!(!out.contains("images/b.png"));
    }

    #[test]
    fn reference_check() {
        assert!(references_image(MD, "images/a.webp"));
        let out = remove_image_occurrence(MD, "images/b.png", 0).unwrap();
        assert!(!references_image(&out, "images/b.png"));
    }
}
