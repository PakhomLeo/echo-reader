//! Text helpers for the SQLite FTS5 full-text index.
//!
//! FTS5's `unicode61` tokenizer keeps a run of CJK characters as one token, so the index
//! also stores CJK uni/bi/trigrams in a dedicated `terms` column and queries are rewritten
//! into quoted terms that match those n-grams.

use std::collections::HashSet;

pub fn is_cjk(ch: char) -> bool {
    matches!(ch as u32,
        0x3040..=0x30FF   // Hiragana, Katakana
        | 0x3400..=0x4DBF // CJK Extension A
        | 0x4E00..=0x9FFF // CJK Unified Ideographs
        | 0xAC00..=0xD7AF // Hangul syllables
        | 0xF900..=0xFAFF // CJK Compatibility Ideographs
        | 0x20000..=0x2FA1F)
}

/// Build the `terms` column: lower-cased words plus CJK 1–3-grams, deduplicated.
pub fn search_terms(text: &str) -> String {
    let mut terms = Vec::new();
    let mut seen = HashSet::new();
    let mut word = String::new();
    let mut cjk_run: Vec<char> = Vec::new();

    fn flush_word(word: &mut String, terms: &mut Vec<String>, seen: &mut HashSet<String>) {
        if !word.is_empty() {
            let value = word.to_lowercase();
            if seen.insert(value.clone()) {
                terms.push(value);
            }
            word.clear();
        }
    }
    fn flush_cjk(run: &mut Vec<char>, terms: &mut Vec<String>, seen: &mut HashSet<String>) {
        for size in 1..=3 {
            if run.len() < size {
                continue;
            }
            for start in 0..=run.len() - size {
                let value = run[start..start + size].iter().collect::<String>();
                if seen.insert(value.clone()) {
                    terms.push(value);
                }
            }
        }
        run.clear();
    }

    for ch in text.chars() {
        if is_cjk(ch) {
            flush_word(&mut word, &mut terms, &mut seen);
            cjk_run.push(ch);
        } else {
            flush_cjk(&mut cjk_run, &mut terms, &mut seen);
            if ch.is_alphanumeric() || ch == '_' {
                word.push(ch);
            } else {
                flush_word(&mut word, &mut terms, &mut seen);
            }
        }
        if terms.len() >= 8000 {
            break;
        }
    }
    flush_word(&mut word, &mut terms, &mut seen);
    flush_cjk(&mut cjk_run, &mut terms, &mut seen);
    terms.join(" ")
}

/// Split a user query into the terms used for matching (also used for snippets).
pub fn query_terms(query: &str) -> Vec<String> {
    let mut terms = Vec::new();
    let mut word = String::new();
    let mut cjk_run: Vec<char> = Vec::new();
    fn flush_word(word: &mut String, terms: &mut Vec<String>) {
        if !word.is_empty() {
            terms.push(word.to_lowercase());
            word.clear();
        }
    }
    fn flush_cjk(run: &mut Vec<char>, terms: &mut Vec<String>) {
        if run.is_empty() {
            return;
        }
        if run.len() <= 3 {
            terms.push(run.iter().collect());
        } else {
            for start in 0..=run.len() - 3 {
                terms.push(run[start..start + 3].iter().collect());
            }
        }
        run.clear();
    }
    for ch in query.chars() {
        if is_cjk(ch) {
            flush_word(&mut word, &mut terms);
            cjk_run.push(ch);
        } else {
            flush_cjk(&mut cjk_run, &mut terms);
            if ch.is_alphanumeric() || ch == '_' {
                word.push(ch);
            } else {
                flush_word(&mut word, &mut terms);
            }
        }
    }
    flush_word(&mut word, &mut terms);
    flush_cjk(&mut cjk_run, &mut terms);
    let mut seen = HashSet::new();
    terms.retain(|term| seen.insert(term.clone()));
    terms.truncate(24);
    terms
}

/// Quote every term so user input can never inject FTS5 query syntax.
pub fn safe_fts_query(query: &str) -> Option<String> {
    let terms = query_terms(query);
    if terms.is_empty() {
        return None;
    }
    Some(
        terms
            .iter()
            .map(|term| format!("\"{}\"", term.replace('"', "\"\"")))
            .collect::<Vec<_>>()
            .join(" AND "),
    )
}

/// "Question" mode: strip common Chinese/English question scaffolding before matching.
pub fn reduce_question_query(query: &str) -> String {
    let mut reduced = query.trim().to_string();
    for phrase in [
        "请问",
        "能否告诉我",
        "告诉我",
        "有哪些",
        "有什么",
        "是什么",
        "为什么",
        "怎么样",
        "如何",
        "哪些",
        "什么",
        "吗",
        "呢",
        "？",
        "?",
    ] {
        reduced = reduced.replace(phrase, " ");
    }
    let stop = [
        "what", "which", "who", "why", "how", "when", "where", "is", "are", "the", "a", "an", "do",
        "does", "did", "about", "of", "in", "on", "tell", "me",
    ];
    let words: Vec<&str> = reduced
        .split_whitespace()
        .filter(|w| !stop.contains(&w.to_lowercase().as_str()))
        .collect();
    let joined = words.join(" ");
    if joined.trim().is_empty() {
        query.trim().to_string()
    } else {
        joined
    }
}

/// Short context window around the first matching term (character based, CJK safe).
pub fn snippet(body: &str, terms: &[String], radius: usize) -> String {
    let lower = body.to_lowercase();
    let mut best: Option<usize> = None;
    for term in terms {
        if let Some(pos) = lower.find(term.as_str()) {
            best = Some(best.map_or(pos, |b| b.min(pos)));
        }
    }
    let chars: Vec<char> = body.chars().collect();
    let center = best.map(|byte| lower[..byte].chars().count()).unwrap_or(0);
    let start = center.saturating_sub(radius);
    let end = (center + radius).min(chars.len());
    let mut out: String = chars[start..end].iter().collect();
    out = out.split_whitespace().collect::<Vec<_>>().join(" ");
    if start > 0 {
        out.insert(0, '…');
    }
    if end < chars.len() {
        out.push('…');
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cjk_terms_include_ngrams() {
        let terms = search_terms("本地优先 Reader");
        for t in ["本", "本地", "本地优", "地优先", "reader"] {
            assert!(terms.split(' ').any(|x| x == t), "missing {t} in {terms}");
        }
    }

    #[test]
    fn query_is_quoted_and_injection_safe() {
        assert_eq!(safe_fts_query("中文").unwrap(), "\"中文\"");
        assert_eq!(
            safe_fts_query("本地优先阅读").unwrap(),
            "\"本地优\" AND \"地优先\" AND \"优先阅\" AND \"先阅读\""
        );
        assert_eq!(
            safe_fts_query("a\" OR b*").unwrap(),
            "\"a\" AND \"or\" AND \"b\""
        );
        assert!(safe_fts_query("  !!! ").is_none());
    }

    #[test]
    fn question_reduction_and_snippet() {
        assert_eq!(
            reduce_question_query("请问 播放器 是什么？").trim(),
            "播放器"
        );
        assert_eq!(reduce_question_query("what is FTS5?"), "FTS5");
        let s = snippet(
            "前面很多文字，然后是关键内容，后面还有",
            &["关键".to_string()],
            4,
        );
        assert!(s.contains("关键"));
        assert!(s.starts_with('…'));
    }
}
