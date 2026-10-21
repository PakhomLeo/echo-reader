pub fn version_of(bytes: &[u8]) -> String {
    format!("blake3:{}", blake3::hash(bytes).to_hex())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_format_is_blake3_hex() {
        let v = version_of(b"hello");
        assert!(v.starts_with("blake3:"));
        assert_eq!(v.len(), 7 + 64);
        assert_eq!(version_of(b"hello"), version_of(b"hello"));
        assert_ne!(version_of(b"hello"), version_of(b"hellp"));
    }
}
