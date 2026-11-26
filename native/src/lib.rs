//! Rust acceleration for VoxCode.
//!
//! Pure-Rust modules hold the logic (tested with `cargo test`); the `python`
//! feature adds the PyO3 module `voxcode_native` that VoxCode imports when
//! installed. Every function has a Python fallback with identical results.

pub mod bm25;
pub mod redact;
pub mod scan;
pub mod tokenizer;
pub mod vad;

#[cfg(feature = "python")]
mod python {
    use crate::{bm25, scan, tokenizer, vad};
    use pyo3::prelude::*;
    use pyo3::types::PyBytes;
    use std::collections::HashSet;
    use std::path::PathBuf;

    #[pyfunction(name = "tokenize")]
    fn tokenize_text(text: &str) -> Vec<String> {
        tokenizer::tokenize(text)
    }

    #[pyfunction(name = "tokenize_many")]
    fn tokenize_texts(py: Python<'_>, texts: Vec<String>) -> Vec<Vec<String>> {
        py.allow_threads(|| tokenizer::tokenize_many(&texts))
    }

    /// BM25 index over texts (tokenized in parallel) or pre-tokenized documents.
    #[pyclass(frozen)]
    struct Bm25Index {
        inner: bm25::Bm25,
    }

    #[pymethods]
    impl Bm25Index {
        #[new]
        fn new(py: Python<'_>, corpus: Vec<Vec<String>>) -> Self {
            Bm25Index {
                inner: py.allow_threads(|| bm25::Bm25::new(&corpus)),
            }
        }

        #[staticmethod]
        fn from_texts(py: Python<'_>, texts: Vec<String>) -> Self {
            let inner = py.allow_threads(|| {
                let docs: Vec<Vec<String>> = tokenizer::tokenize_many(&texts)
                    .into_iter()
                    .map(|d| {
                        if d.is_empty() {
                            vec!["_empty_".to_string()]
                        } else {
                            d
                        }
                    })
                    .collect();
                bm25::Bm25::new(&docs)
            });
            Bm25Index { inner }
        }

        fn __len__(&self) -> usize {
            self.inner.len()
        }

        fn scores(&self, query: Vec<String>) -> Vec<f64> {
            self.inner.scores(&query)
        }

        fn top_k(&self, query: Vec<String>, k: usize) -> Vec<(usize, f64)> {
            self.inner.top_k(&query, k)
        }
    }

    #[pyfunction(name = "scan_repository")]
    #[pyo3(signature = (root, max_file_bytes, ignored_dirs, root_ignored_dirs, ignored_suffixes, extensions))]
    fn scan_repo(
        py: Python<'_>,
        root: PathBuf,
        max_file_bytes: u64,
        ignored_dirs: HashSet<String>,
        root_ignored_dirs: HashSet<String>,
        ignored_suffixes: Vec<String>,
        extensions: HashSet<String>,
    ) -> Vec<PathBuf> {
        let rules = scan::ScanRules {
            ignored_dirs,
            root_ignored_dirs,
            ignored_suffixes,
            extensions,
            max_file_bytes,
        };
        py.allow_threads(|| scan::scan(&root, &rules))
    }

    #[pyfunction(name = "redact")]
    fn redact_text(py: Python<'_>, text: String) -> (String, usize) {
        py.allow_threads(|| crate::redact::redact(&text))
    }

    fn floats(bytes: &[u8]) -> Vec<f32> {
        bytes
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
            .collect()
    }

    fn to_bytes<'py>(py: Python<'py>, audio: &[f32]) -> Bound<'py, PyBytes> {
        let raw: Vec<u8> = audio.iter().flat_map(|x| x.to_le_bytes()).collect();
        PyBytes::new(py, &raw)
    }

    /// Frames are little-endian float32 bytes (`ndarray.tobytes()`).
    #[pyclass]
    struct Segmenter {
        inner: vad::Segmenter,
    }

    #[pymethods]
    impl Segmenter {
        #[new]
        fn new(silence_seconds: f64, threshold: f64) -> Self {
            Segmenter {
                inner: vad::Segmenter::new(silence_seconds, threshold),
            }
        }

        #[getter]
        fn threshold(&self) -> f64 {
            self.inner.threshold()
        }

        #[getter]
        fn in_speech(&self) -> bool {
            self.inner.in_speech
        }

        fn current_audio<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
            to_bytes(py, &self.inner.current_audio())
        }

        /// Returns None, ("start", None) or ("end", bytes | None).
        fn feed<'py>(
            &mut self,
            py: Python<'py>,
            frame: &[u8],
        ) -> Option<(&'static str, Option<Bound<'py, PyBytes>>)> {
            match self.inner.feed(&floats(frame))? {
                vad::Event::Start => Some(("start", None)),
                vad::Event::End(audio) => Some(("end", audio.map(|a| to_bytes(py, &a)))),
            }
        }

        /// Feed a whole buffer (any number of 30 ms frames); returns every event.
        fn feed_many<'py>(
            &mut self,
            py: Python<'py>,
            audio: &[u8],
        ) -> Vec<(&'static str, Option<Bound<'py, PyBytes>>)> {
            let samples = floats(audio);
            let mut events = Vec::new();
            for frame in samples.chunks_exact(vad::FRAME) {
                match self.inner.feed(frame) {
                    Some(vad::Event::Start) => events.push(("start", None)),
                    Some(vad::Event::End(a)) => events.push(("end", a.map(|a| to_bytes(py, &a)))),
                    None => {}
                }
            }
            events
        }
    }

    #[pymodule]
    fn voxcode_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        m.add_function(wrap_pyfunction!(tokenize_text, m)?)?;
        m.add_function(wrap_pyfunction!(tokenize_texts, m)?)?;
        m.add_function(wrap_pyfunction!(scan_repo, m)?)?;
        m.add_function(wrap_pyfunction!(redact_text, m)?)?;
        m.add_class::<Bm25Index>()?;
        m.add_class::<Segmenter>()?;
        Ok(())
    }
}
