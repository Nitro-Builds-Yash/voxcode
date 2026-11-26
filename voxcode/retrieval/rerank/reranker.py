"""Cross-encoder reranking (optional; needs `transformers` + `torch`)."""

from __future__ import annotations

from voxcode.models.retrieval_result import RetrievalResult


class CrossEncoderReranker:
    def __init__(self, model_name: str):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        if self.device == "cuda":
            self.model.half()
        self.model.to(self.device).eval()

    def rerank(
        self, query: str, results: list[RetrievalResult], top_k: int = 5, batch_size: int = 16
    ) -> list[RetrievalResult]:
        if not results:
            return []
        scores: list[float] = []
        for start in range(0, len(results), batch_size):
            batch = results[start : start + batch_size]
            inputs = self.tokenizer(
                [[query, r.content] for r in batch],
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            ).to(self.device)
            with self._torch.inference_mode():
                logits = self.model(**inputs).logits.view(-1)
            scores.extend(logits.float().cpu().tolist())

        ranked = [r.model_copy(update={"rerank_score": s}) for r, s in zip(results, scores, strict=True)]
        ranked.sort(key=lambda r: r.rerank_score or 0.0, reverse=True)
        return ranked[:top_k]
