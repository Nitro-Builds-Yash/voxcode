//! Repository scanner; same rules and order as `scanner.scan_repository`.

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

pub struct ScanRules {
    pub ignored_dirs: HashSet<String>,
    pub root_ignored_dirs: HashSet<String>,
    pub ignored_suffixes: Vec<String>,
    pub extensions: HashSet<String>, // lower-case, with the dot
    pub max_file_bytes: u64,
}

fn skip_dir(name: &str, parent: &Path, root: &Path, rules: &ScanRules) -> bool {
    if rules.ignored_dirs.contains(name) || name.ends_with(".egg-info") {
        return true;
    }
    if parent == root && rules.root_ignored_dirs.contains(name) {
        return true;
    }
    parent.join(name).join("pyvenv.cfg").exists()
}

fn has_known_extension(name: &str, rules: &ScanRules) -> bool {
    // PurePath.suffix: text from the last dot, unless the name starts with it.
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => {
            rules.extensions.contains(&name[i..].to_lowercase())
        }
        _ => false,
    }
}

pub fn scan(root: &Path, rules: &ScanRules) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let Ok(file_type) = entry.file_type() else {
                continue;
            };
            let name = entry.file_name();
            let Some(name) = name.to_str() else { continue };
            if file_type.is_dir() {
                if !skip_dir(name, &dir, root, rules) {
                    stack.push(entry.path());
                }
            } else if file_type.is_file() {
                if rules
                    .ignored_suffixes
                    .iter()
                    .any(|s| name.ends_with(s.as_str()))
                    || !has_known_extension(name, rules)
                {
                    continue;
                }
                match entry.metadata() {
                    Ok(meta) if meta.len() <= rules.max_file_bytes => files.push(entry.path()),
                    _ => {}
                }
            }
            // Symlinks (to files or directories) are skipped, like os.walk + is_symlink().
        }
    }
    files.sort();
    files
}
