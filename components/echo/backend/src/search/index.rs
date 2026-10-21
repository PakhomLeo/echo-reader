//! SQLite FTS5 search index.
//!
//! The library folders are the single source of truth. This database is a cache that can
//! be deleted at any time and rebuilt from `manifest.json`, `article.md`,
//! `transcript.json` and the optional `ocr.json` sidecar of each item.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use rusqlite::{Connection, OptionalExtension, params};
use serde::Serialize;

use super::extract::{Chunk, extract_chunks};
use super::text::{query_terms, reduce_question_query, safe_fts_query, search_terms, snippet};
use crate::models::item::ValidItem;

/// Bump when the schema or the chunking rules change; old indexes are rebuilt automatically.
pub const INDEX_SCHEMA_VERSION: &str = "1";
pub const OCR_SIDECAR: &str = "ocr.json";

/// Snapshot of what the index needs from one library item (cheap to clone into a worker).
#[derive(Debug, Clone)]
pub struct DocSource {
    pub item_id: String,
    pub folder_name: String,
    pub folder_path: PathBuf,
    pub title: String,
    pub author: Option<String>,
    pub article_path: Option<PathBuf>,
    pub transcript_path: Option<PathBuf>,
}

impl DocSource {
    pub fn from_item(item: &ValidItem) -> Self {
        Self {
            item_id: item.id.clone(),
            folder_name: item.folder_name.clone(),
            folder_path: item.folder_path.clone(),
            title: item
                .manifest
                .source
                .title
                .clone()
                .filter(|t| !t.trim().is_empty())
                .unwrap_or_else(|| item.folder_name.clone()),
            author: item.manifest.source.author.clone(),
            article_path: item.has_article.then(|| item.article_path()),
            transcript_path: item.has_transcript.then(|| item.transcript_path()),
        }
    }

    fn files(&self) -> Vec<PathBuf> {
        let mut files = vec![self.folder_path.join("manifest.json")];
        files.extend(self.article_path.clone());
        files.extend(self.transcript_path.clone());
        files.push(self.folder_path.join(OCR_SIDECAR));
        files
    }

    /// Cheap change detector: size + mtime of every source file.
    pub fn stat_signature(&self) -> String {
        self.files()
            .iter()
            .map(|path| match std::fs::metadata(path) {
                Ok(meta) => {
                    let mtime = meta
                        .modified()
                        .ok()
                        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                        .map(|d| d.as_nanos())
                        .unwrap_or(0);
                    format!("{}:{mtime}", meta.len())
                }
                Err(_) => "-".to_owned(),
            })
            .collect::<Vec<_>>()
            .join("|")
    }

    /// Content hash of every source file plus the derived metadata.
    pub fn fingerprint(&self) -> String {
        let mut hasher = blake3::Hasher::new();
        hasher.update(INDEX_SCHEMA_VERSION.as_bytes());
        hasher.update(self.title.as_bytes());
        hasher.update(b"\0");
        hasher.update(self.author.as_deref().unwrap_or("").as_bytes());
        for path in self.files() {
            hasher.update(b"\0file\0");
            match std::fs::read(&path) {
                Ok(bytes) => {
                    hasher.update(&(bytes.len() as u64).to_le_bytes());
                    hasher.update(&bytes);
                }
                Err(_) => {
                    hasher.update(b"missing");
                }
            }
        }
        hasher.finalize().to_hex().to_string()
    }
}

#[derive(Debug, Default, Clone, Serialize, PartialEq, Eq)]
pub struct SyncReport {
    pub added: usize,
    pub updated: usize,
    pub removed: usize,
    pub unchanged: usize,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct IndexStatus {
    pub index_path: String,
    pub schema_version: String,
    pub indexed_items: usize,
    pub library_items: usize,
    pub chunks: usize,
    /// Items whose files changed since they were indexed.
    pub stale_items: Vec<String>,
    /// Library items that are not in the index yet.
    pub missing_items: Vec<String>,
    /// Index entries whose item no longer exists in the library.
    pub orphan_items: Vec<String>,
    pub up_to_date: bool,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SearchHit {
    pub item_id: String,
    pub folder_name: String,
    pub title: String,
    /// `title`, `article`, `transcript` or `ocr`.
    pub kind: String,
    /// Block number for articles, segment id for transcripts, image path for OCR.
    pub locator: String,
    pub start_ms: Option<i64>,
    pub snippet: String,
    pub score: f64,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SearchResponse {
    pub query: String,
    pub effective_query: String,
    pub hits: Vec<SearchHit>,
}

pub struct SearchIndex {
    path: PathBuf,
    conn: Connection,
}

impl SearchIndex {
    pub fn open(path: &Path) -> rusqlite::Result<Self> {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let conn = Connection::open(path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        let mut index = Self {
            path: path.to_path_buf(),
            conn,
        };
        index.ensure_schema()?;
        Ok(index)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn ensure_schema(&mut self) -> rusqlite::Result<()> {
        self.conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);",
        )?;
        let version: Option<String> = self
            .conn
            .query_row(
                "SELECT value FROM meta WHERE key='schema_version'",
                [],
                |r| r.get(0),
            )
            .optional()?;
        if version.as_deref() != Some(INDEX_SCHEMA_VERSION) {
            self.conn.execute_batch(
                "DROP TABLE IF EXISTS docs;
                 DROP TABLE IF EXISTS chunks;",
            )?;
        }
        self.conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS docs(
                 item_id TEXT PRIMARY KEY,
                 folder_name TEXT NOT NULL,
                 title TEXT NOT NULL,
                 stat_sig TEXT NOT NULL,
                 fingerprint TEXT NOT NULL,
                 chunk_count INTEGER NOT NULL
             );
             CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                 item_id UNINDEXED,
                 kind UNINDEXED,
                 locator UNINDEXED,
                 start_ms UNINDEXED,
                 seq UNINDEXED,
                 title,
                 body,
                 terms,
                 tokenize='unicode61 remove_diacritics 2'
             );",
        )?;
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?1)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params![INDEX_SCHEMA_VERSION],
        )?;
        Ok(())
    }

    fn indexed_docs(&self) -> rusqlite::Result<HashMap<String, (String, String)>> {
        let mut stmt = self
            .conn
            .prepare("SELECT item_id, stat_sig, fingerprint FROM docs")?;
        let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, (r.get(1)?, r.get(2)?))))?;
        rows.collect()
    }

    /// Bring the index in line with the library: add new items, re-index changed ones
    /// (detected by size/mtime, confirmed by content hash) and drop removed ones.
    pub fn sync(&mut self, sources: &[DocSource]) -> rusqlite::Result<SyncReport> {
        let existing = self.indexed_docs()?;
        let mut report = SyncReport::default();
        let live: HashSet<&str> = sources.iter().map(|s| s.item_id.as_str()).collect();
        let tx = self.conn.transaction()?;
        for source in sources {
            let stat_sig = source.stat_signature();
            match existing.get(&source.item_id) {
                Some((old_sig, _)) if *old_sig == stat_sig => report.unchanged += 1,
                Some((_, old_fp)) => {
                    let fingerprint = source.fingerprint();
                    if *old_fp == fingerprint {
                        tx.execute(
                            "UPDATE docs SET stat_sig=?2 WHERE item_id=?1",
                            params![source.item_id, stat_sig],
                        )?;
                        report.unchanged += 1;
                    } else {
                        write_doc(&tx, source, &stat_sig, &fingerprint)?;
                        report.updated += 1;
                    }
                }
                None => {
                    let fingerprint = source.fingerprint();
                    write_doc(&tx, source, &stat_sig, &fingerprint)?;
                    report.added += 1;
                }
            }
        }
        for item_id in existing.keys() {
            if !live.contains(item_id.as_str()) {
                delete_doc(&tx, item_id)?;
                report.removed += 1;
            }
        }
        tx.commit()?;
        Ok(report)
    }

    /// Re-index a single item immediately (used after edits made through the API).
    pub fn reindex_one(&mut self, source: &DocSource) -> rusqlite::Result<()> {
        let tx = self.conn.transaction()?;
        write_doc(&tx, source, &source.stat_signature(), &source.fingerprint())?;
        tx.commit()
    }

    /// Report which entries are stale without modifying the index.
    pub fn status(&self, sources: &[DocSource]) -> rusqlite::Result<IndexStatus> {
        let existing = self.indexed_docs()?;
        let live: HashSet<&str> = sources.iter().map(|s| s.item_id.as_str()).collect();
        let mut stale = Vec::new();
        let mut missing = Vec::new();
        for source in sources {
            match existing.get(&source.item_id) {
                None => missing.push(source.item_id.clone()),
                Some((sig, fp)) => {
                    if *sig != source.stat_signature() && *fp != source.fingerprint() {
                        stale.push(source.item_id.clone());
                    }
                }
            }
        }
        let mut orphans: Vec<String> = existing
            .keys()
            .filter(|id| !live.contains(id.as_str()))
            .cloned()
            .collect();
        stale.sort();
        missing.sort();
        orphans.sort();
        let chunks: i64 = self
            .conn
            .query_row("SELECT count(*) FROM chunks", [], |r| r.get(0))?;
        let up_to_date = stale.is_empty() && missing.is_empty() && orphans.is_empty();
        Ok(IndexStatus {
            index_path: self.path.display().to_string(),
            schema_version: INDEX_SCHEMA_VERSION.to_owned(),
            indexed_items: existing.len(),
            library_items: sources.len(),
            chunks: chunks as usize,
            stale_items: stale,
            missing_items: missing,
            orphan_items: orphans,
            up_to_date,
        })
    }

    pub fn search(
        &self,
        query: &str,
        question: bool,
        limit: usize,
    ) -> rusqlite::Result<SearchResponse> {
        let effective = if question {
            reduce_question_query(query)
        } else {
            query.trim().to_owned()
        };
        let Some(fts_query) = safe_fts_query(&effective) else {
            return Ok(SearchResponse {
                query: query.to_owned(),
                effective_query: effective,
                hits: Vec::new(),
            });
        };
        let terms = query_terms(&effective);
        let mut stmt = self.conn.prepare(
            "SELECT c.item_id, d.folder_name, d.title, c.kind, c.locator, c.start_ms, c.body,
                    bm25(chunks, 0.0, 0.0, 0.0, 0.0, 0.0, 4.0, 1.0, 1.0) AS score
             FROM chunks c JOIN docs d ON d.item_id = c.item_id
             WHERE chunks MATCH ?1
             ORDER BY score, c.item_id, c.seq
             LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![fts_query, limit.clamp(1, 200) as i64], |r| {
            let body: String = r.get(6)?;
            let start_ms: Option<String> = r.get(5)?;
            Ok(SearchHit {
                item_id: r.get(0)?,
                folder_name: r.get(1)?,
                title: r.get(2)?,
                kind: r.get(3)?,
                locator: r.get(4)?,
                start_ms: start_ms.and_then(|v| v.parse().ok()),
                snippet: snippet(&body, &terms, 60),
                // Round so results compare equal across rebuilds despite float noise.
                score: (r.get::<_, f64>(7)? * 1_000_000.0).round() / 1_000_000.0,
            })
        })?;
        Ok(SearchResponse {
            query: query.to_owned(),
            effective_query: effective,
            hits: rows.collect::<rusqlite::Result<Vec<_>>>()?,
        })
    }
}

/// Delete the index file (and SQLite side files) and build it again from the library.
pub fn rebuild(path: &Path, sources: &[DocSource]) -> rusqlite::Result<(SearchIndex, SyncReport)> {
    for suffix in ["", "-wal", "-shm", "-journal"] {
        let mut file = path.as_os_str().to_owned();
        file.push(suffix);
        let _ = std::fs::remove_file(PathBuf::from(file));
    }
    let mut index = SearchIndex::open(path)?;
    let report = index.sync(sources)?;
    Ok((index, report))
}

fn delete_doc(tx: &rusqlite::Transaction<'_>, item_id: &str) -> rusqlite::Result<()> {
    tx.execute("DELETE FROM chunks WHERE item_id=?1", params![item_id])?;
    tx.execute("DELETE FROM docs WHERE item_id=?1", params![item_id])?;
    Ok(())
}

fn write_doc(
    tx: &rusqlite::Transaction<'_>,
    source: &DocSource,
    stat_sig: &str,
    fingerprint: &str,
) -> rusqlite::Result<()> {
    delete_doc(tx, &source.item_id)?;
    let chunks: Vec<Chunk> = extract_chunks(source);
    let mut insert = tx.prepare(
        "INSERT INTO chunks(item_id, kind, locator, start_ms, seq, title, body, terms)
         VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
    )?;
    for (seq, chunk) in chunks.iter().enumerate() {
        let title_field = if chunk.kind == "title" {
            source.title.as_str()
        } else {
            ""
        };
        let terms = search_terms(&format!("{title_field} {}", chunk.body));
        insert.execute(params![
            source.item_id,
            chunk.kind,
            chunk.locator,
            chunk.start_ms.map(|v| v.to_string()),
            seq as i64,
            title_field,
            chunk.body,
            terms,
        ])?;
    }
    tx.execute(
        "INSERT INTO docs(item_id, folder_name, title, stat_sig, fingerprint, chunk_count)
         VALUES(?1, ?2, ?3, ?4, ?5, ?6)",
        params![
            source.item_id,
            source.folder_name,
            source.title,
            stat_sig,
            fingerprint,
            chunks.len() as i64
        ],
    )?;
    Ok(())
}
