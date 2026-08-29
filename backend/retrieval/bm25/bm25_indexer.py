from __future__ import annotations
import math
from typing import List

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    class BM25Okapi:
        def __init__(self, corpus, k1=1.5, b=0.75):
            self.k1 = k1
            self.b = b
            self.corpus = corpus
            self.corpus_size = len(corpus)
            self.doc_lens = [len(doc) for doc in corpus]
            self.avgdl = sum(self.doc_lens) / self.corpus_size if self.corpus_size > 0 else 1.0
            self.doc_freqs = {}
            self.nd = {}
            for doc in corpus:
                for word in set(doc):
                    self.doc_freqs[word] = self.doc_freqs.get(word, 0) + 1

        def get_scores(self, query):
            scores = [0.0] * self.corpus_size
            for q in query:
                q = q.lower()
                df = self.doc_freqs.get(q, 0)
                if df == 0: continue
                idf = math.log(1 + (self.corpus_size - df + 0.5) / (df + 0.5))
                for i, doc in enumerate(self.corpus):
                    freq = doc.count(q)
                    if freq == 0: continue
                    num = freq * (self.k1 + 1)
                    den = freq + self.k1 * (1 - self.b + self.b * (self.doc_lens[i] / self.avgdl))
                    scores[i] += idf * (num / den)
            return scores


class BM25Indexer:
    def build(self, chunks):
        corpus = []
        for chunk in chunks:
            text = f"{chunk.symbol_name} {chunk.content}"
            tokens = text.lower().split()
            corpus.append(tokens)
        bm25 = BM25Okapi(corpus)
        return bm25
