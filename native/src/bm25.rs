//! BM25 Okapi with an inverted index.
//!
//! Scores match `rank_bm25.BM25Okapi` (k1=1.5, b=0.75, epsilon=0.25,
//! negative IDFs floored to epsilon * mean IDF) to the last bit: the same
//! floating-point operations happen in the same order. Only documents that
//! contain a query term are touched, instead of every document per term.

use std::collections::HashMap;

pub struct Bm25 {
    postings: HashMap<String, Vec<(u32, f64)>>,
    idf: HashMap<String, f64>,
    norm: Vec<f64>, // k1 * (1 - b + b * len / avgdl), per document
    k1: f64,
    n_docs: usize,
}

impl Bm25 {
    pub fn new(corpus: &[Vec<String>]) -> Self {
        Self::with_params(corpus, 1.5, 0.75, 0.25)
    }

    pub fn with_params(corpus: &[Vec<String>], k1: f64, b: f64, epsilon: f64) -> Self {
        let n = corpus.len();
        let mut postings: HashMap<String, Vec<(u32, f64)>> = HashMap::new();
        let mut total_len = 0usize;
        for (doc, tokens) in corpus.iter().enumerate() {
            total_len += tokens.len();
            let mut counts: HashMap<&str, f64> = HashMap::new();
            for t in tokens {
                *counts.entry(t.as_str()).or_insert(0.0) += 1.0;
            }
            for (t, c) in counts {
                postings
                    .entry(t.to_string())
                    .or_default()
                    .push((doc as u32, c));
            }
        }
        let avgdl = if n == 0 {
            0.0
        } else {
            total_len as f64 / n as f64
        };
        let mut idf = HashMap::with_capacity(postings.len());
        let mut idf_sum = 0.0;
        let mut negative = Vec::new();
        for (t, docs) in &postings {
            let df = docs.len() as f64;
            let value = (n as f64 - df + 0.5).ln() - (df + 0.5).ln();
            idf_sum += value;
            if value < 0.0 {
                negative.push(t.clone());
            }
            idf.insert(t.clone(), value);
        }
        if !idf.is_empty() {
            let eps = epsilon * (idf_sum / idf.len() as f64);
            for t in negative {
                idf.insert(t, eps);
            }
        }
        let norm = corpus
            .iter()
            .map(|d| k1 * (1.0 - b + b * d.len() as f64 / avgdl))
            .collect();
        Bm25 {
            postings,
            idf,
            norm,
            k1,
            n_docs: n,
        }
    }

    pub fn len(&self) -> usize {
        self.n_docs
    }

    pub fn is_empty(&self) -> bool {
        self.n_docs == 0
    }

    pub fn scores(&self, query: &[String]) -> Vec<f64> {
        let mut scores = vec![0.0f64; self.n_docs];
        for q in query {
            let (Some(docs), Some(&idf)) = (self.postings.get(q), self.idf.get(q)) else {
                continue;
            };
            for &(doc, tf) in docs {
                let d = doc as usize;
                scores[d] += idf * (tf * (self.k1 + 1.0) / (tf + self.norm[d]));
            }
        }
        scores
    }

    /// Indices and scores of the best `k` documents with a positive score,
    /// best first; ties keep corpus order (like a stable argsort).
    pub fn top_k(&self, query: &[String], k: usize) -> Vec<(usize, f64)> {
        let scores = self.scores(query);
        let mut hits: Vec<(usize, f64)> = scores
            .into_iter()
            .enumerate()
            .filter(|(_, s)| *s > 0.0)
            .collect();
        let by_rank = |a: &(usize, f64), b: &(usize, f64)| b.1.total_cmp(&a.1).then(a.0.cmp(&b.0));
        if hits.len() > k && k > 0 {
            hits.select_nth_unstable_by(k - 1, by_rank);
            hits.truncate(k);
        } else if k == 0 {
            hits.clear();
        }
        hits.sort_by(by_rank);
        hits
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(s: &str) -> Vec<String> {
        s.split_whitespace().map(String::from).collect()
    }

    #[test]
    fn ranks_matching_docs() {
        let index = Bm25::new(&[
            doc("login user password"),
            doc("logout user"),
            doc("render page"),
            doc("cache page"),
        ]);
        let top = index.top_k(&doc("password"), 3);
        assert_eq!(top.len(), 1);
        assert_eq!(top[0].0, 0);
        assert!(index.top_k(&doc("missing"), 3).is_empty());
    }
}
