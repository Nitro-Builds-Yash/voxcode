//! Code-aware tokenizer; byte-for-byte compatible with
//! `voxcode/retrieval/bm25/tokenizer.py`.
//!
//! `getUserById(user_id)` -> getuserbyid, get, user, by, id, user_id, user, id

use rayon::prelude::*;
use regex::Regex;
use std::sync::OnceLock;

const STOPWORDS: &[&str] = &[
    "a", "an", "the", "is", "are", "was", "be", "to", "of", "and", "or", "in", "on", "for", "with",
    "this", "that", "it", "do", "does", "how", "what", "where", "which", "who", "why", "me", "my",
    "i", "we", "you", "show", "find", "explain", "please", "can", "code", "self", "return",
];

fn identifier_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    // Same as Python's `[A-Za-z_][A-Za-z0-9_]*|\d+` (\d is Unicode in both).
    RE.get_or_init(|| Regex::new(r"[A-Za-z_][A-Za-z0-9_]*|\d+").unwrap())
}

fn is_digit(c: char) -> bool {
    // Python `\d` on str == Unicode category Nd; the regex crate agrees.
    c.is_ascii_digit() || (!c.is_ascii() && is_nd(c))
}

fn is_nd(c: char) -> bool {
    static RE: OnceLock<Regex> = OnceLock::new();
    let mut buf = [0u8; 4];
    RE.get_or_init(|| Regex::new(r"^\d$").unwrap())
        .is_match(c.encode_utf8(&mut buf))
}

/// Emulates `[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+` with findall.
fn camel_parts(piece: &str, out: &mut Vec<String>) {
    let chars: Vec<char> = piece.chars().collect();
    let n = chars.len();
    let upper = |i: usize| i < n && chars[i].is_ascii_uppercase();
    let lower = |i: usize| i < n && chars[i].is_ascii_lowercase();
    let mut i = 0;
    while i < n {
        let mut run = 0;
        while upper(i + run) {
            run += 1;
        }
        let end = if run >= 2 && lower(i + run) {
            i + run - 1 // acronym followed by a capitalised word: "HTTPServer" -> HTTP
        } else if lower(i) || (upper(i) && lower(i + 1)) {
            let mut j = if upper(i) { i + 1 } else { i };
            while lower(j) {
                j += 1;
            }
            j
        } else if run >= 1 {
            i + run
        } else if is_digit(chars[i]) {
            let mut j = i;
            while j < n && is_digit(chars[j]) {
                j += 1;
            }
            j
        } else {
            i += 1;
            continue;
        };
        out.push(chars[i..end].iter().collect::<String>().to_lowercase());
        i = end;
    }
}

pub fn tokenize(text: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut parts = Vec::new();
    for m in identifier_re().find_iter(text) {
        let identifier = m.as_str();
        let lowered = identifier.to_lowercase();
        parts.clear();
        for piece in identifier.split('_') {
            camel_parts(piece, &mut parts);
        }
        if parts.len() > 1 || !parts.contains(&lowered) {
            tokens.push(lowered);
        }
        tokens.extend(parts.iter().cloned());
    }
    tokens.retain(|t| t.chars().count() > 1 && !STOPWORDS.contains(&t.as_str()));
    tokens
}

pub fn tokenize_many(texts: &[String]) -> Vec<Vec<String>> {
    texts.par_iter().map(|t| tokenize(t)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_identifiers() {
        assert_eq!(
            tokenize("getUserById(user_id)"),
            [
                "getuserbyid",
                "get",
                "user",
                "by",
                "id",
                "user_id",
                "user",
                "id"
            ]
        );
        assert_eq!(
            tokenize("HTTPServer parseJSON2"),
            [
                "httpserver",
                "http",
                "server",
                "parsejson2",
                "parse",
                "json"
            ]
        );
        assert_eq!(tokenize("how is the code"), Vec::<String>::new());
    }
}
