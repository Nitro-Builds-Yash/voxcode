//! Secret redaction; same patterns and results as `voxcode/core/redaction.py`.

use regex::{Captures, Regex};
use std::sync::OnceLock;

struct Patterns {
    simple: Vec<(&'static str, Regex)>,
    assignment: Regex,
}

fn patterns() -> &'static Patterns {
    static P: OnceLock<Patterns> = OnceLock::new();
    P.get_or_init(|| {
        let simple = [
            ("private-key", r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"),
            ("aws-access-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
            ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
            ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
            ("openai-key", r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"),
            ("google-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
            ("groq-key", r"\bgsk_[A-Za-z0-9]{20,}"),
            ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
            ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
            ("connection-string", r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]{3,}@"),
        ]
        .into_iter()
        .map(|(k, p)| (k, Regex::new(p).unwrap()))
        .collect();
        // Python uses a back-reference for the optional quote; spelled out here.
        let name = r"\b[\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|private[_-]?key|token)[\w.-]*\s*[:=]\s*";
        let value = r#"[^\s"',;]{6,}"#;
        let assignment =
            Regex::new(&format!(r#"(?i)({name})(?:"({value})"|'({value})'|({value}))"#)).unwrap();
        Patterns { simple, assignment }
    })
}

const PLACEHOLDERS: &[&str] = &[
    "changeme",
    "change-me",
    "example",
    "xxxxxx",
    "your-key",
    "placeholder",
    "redacted",
];
const SAFE_PREFIXES: &[&str] = &[
    "[REDACTED",
    "os.environ",
    "settings.",
    "process.env",
    "$",
    "{",
    "<",
];

pub fn redact(text: &str) -> (String, usize) {
    let p = patterns();
    let mut count = 0;
    let mut text = text.to_string();
    for (kind, re) in &p.simple {
        let replaced = re.replace_all(&text, |_: &Captures| {
            count += 1;
            format!("[REDACTED:{kind}]")
        });
        if let std::borrow::Cow::Owned(s) = replaced {
            text = s;
        }
    }
    let replaced = p.assignment.replace_all(&text, |c: &Captures| {
        let (quote, value) = if let Some(v) = c.get(2) {
            ("\"", v.as_str())
        } else if let Some(v) = c.get(3) {
            ("'", v.as_str())
        } else {
            ("", c.get(4).unwrap().as_str())
        };
        if PLACEHOLDERS.contains(&value.to_lowercase().as_str())
            || SAFE_PREFIXES.iter().any(|s| value.starts_with(s))
        {
            return c[0].to_string();
        }
        count += 1;
        format!("{}{quote}[REDACTED:secret]{quote}", &c[1])
    });
    (replaced.into_owned(), count)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_keys_and_assignments() {
        let (out, n) =
            redact("key = \"sk-ant-abcdefghijklmnopqrstuv\"\npassword: hunter22\napi_key=changeme");
        assert_eq!(n, 2);
        assert!(
            out.contains("[REDACTED:anthropic-key]") && out.contains("password: [REDACTED:secret]")
        );
        assert!(out.contains("api_key=changeme"));
    }
}
