use std::path::Path;

use crate::models::api::Problem;
use crate::models::item::ValidItem;

use super::scanner::scan_library_or_single;

#[derive(Debug, Default)]
pub struct ItemIndex {
    items: Vec<ValidItem>,
    problems: Vec<Problem>,
}

impl ItemIndex {
    pub fn scan(root: &Path) -> Self {
        let result = scan_library_or_single(root);
        Self {
            items: result.items,
            problems: result.problems,
        }
    }

    pub fn items(&self) -> impl Iterator<Item = &ValidItem> {
        self.items.iter()
    }

    pub fn get(&self, id: &str) -> Option<&ValidItem> {
        self.items.iter().find(|i| i.id == id)
    }

    pub fn problems(&self) -> &[Problem] {
        &self.problems
    }
}

pub fn sorted_summaries(index: &ItemIndex) -> Vec<&ValidItem> {
    let mut all: Vec<&ValidItem> = index.items().collect();
    all.sort_by(|a, b| {
        b.manifest
            .updated_at
            .cmp(&a.manifest.updated_at)
            .then_with(|| a.folder_name.cmp(&b.folder_name))
    });
    all
}
