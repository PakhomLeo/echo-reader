use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct ContentJson {
    pub schema_version: u32,
    pub blocks: Vec<Block>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Block {
    Heading {
        level: u32,
        text: String,
    },
    Paragraph {
        markdown: String,
    },
    Image {
        path: String,
        #[serde(default)]
        alt: Option<String>,
        #[serde(default)]
        caption: Option<String>,
    },
    Blockquote {
        markdown: String,
    },
    UnorderedList {
        markdown: String,
    },
    OrderedList {
        markdown: String,
    },
    Code {
        markdown: String,
    },
    HorizontalRule,
}

impl ContentJson {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != 1 {
            return Err(format!("不支持的 schema_version: {}", self.schema_version));
        }
        for block in &self.blocks {
            if let Block::Heading { level, .. } = block
                && !(1..=6).contains(level)
            {
                return Err(format!("非法的标题级别: {level}"));
            }
        }
        Ok(())
    }
}
