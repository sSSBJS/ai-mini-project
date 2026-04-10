from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from pypdf import PdfReader

from semiconductor_agent.models import EvidenceItem
from semiconductor_agent.runtime import RuntimeConfig

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - optional runtime dependency
    SentenceTransformer = None


class ChunkRecord:
    def __init__(
        self,
        chunk_id: str,
        text: str,
        source_path: Path,
        source_type: str,
        page: int,
        published_at: Optional[date],
    ):
        self.chunk_id = chunk_id
        self.text = text
        self.source_path = source_path
        self.source_type = source_type
        self.page = page
        self.published_at = published_at


class BM25Retriever:
    def __init__(self, chunks: Sequence[ChunkRecord], k1: float = 1.5, b: float = 0.75):
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b
        self.doc_tokens = [self._tokenize(chunk.text) for chunk in self.chunks]
        self.doc_freq = defaultdict(int)
        self.avg_doc_len = 1.0
        total_len = 0
        for tokens in self.doc_tokens:
            total_len += len(tokens)
            for token in set(tokens):
                self.doc_freq[token] += 1
        if self.doc_tokens:
            self.avg_doc_len = total_len / float(len(self.doc_tokens))

    def search(self, query: str, top_k: int = 4) -> List[Tuple[ChunkRecord, float]]:
        q_tokens = self._tokenize(query)
        scores = []
        doc_count = len(self.doc_tokens) or 1
        for chunk, tokens in zip(self.chunks, self.doc_tokens):
            score = 0.0
            term_freq = Counter(tokens)
            doc_len = len(tokens) or 1
            for token in q_tokens:
                df = self.doc_freq.get(token, 0)
                if not df:
                    continue
                idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
                freq = term_freq.get(token, 0)
                numerator = freq * (self.k1 + 1)
                denominator = freq + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_len)
                score += idf * numerator / denominator
            scores.append((chunk, score))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:top_k]

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"[A-Za-z0-9가-힣\.\-]+", text.lower())


class E5CompatibleDenseRetriever:
    def __init__(self, chunks: Sequence[ChunkRecord], model_name: str):
        self.chunks = list(chunks)
        self.model_name = model_name
        self.model = None
        self.chunk_vectors = []
        if SentenceTransformer is not None:
            try:
                self.model = SentenceTransformer(model_name)
                self.chunk_vectors = self.model.encode(
                    [self._query_instruction(chunk.text, is_query=False) for chunk in self.chunks],
                    normalize_embeddings=True,
                )
            except Exception:
                self.model = None
                self.chunk_vectors = [self._fallback_vector(chunk.text) for chunk in self.chunks]
        else:
            self.chunk_vectors = [self._fallback_vector(chunk.text) for chunk in self.chunks]

    def search(self, query: str, top_k: int = 4) -> List[Tuple[ChunkRecord, float]]:
        if self.model is not None:
            query_vector = self.model.encode(
                [self._query_instruction(query, is_query=True)],
                normalize_embeddings=True,
            )[0]
            scores = []
            for chunk, vector in zip(self.chunks, self.chunk_vectors):
                score = sum(a * b for a, b in zip(query_vector, vector))
                scores.append((chunk, float(score)))
            return sorted(scores, key=lambda item: item[1], reverse=True)[:top_k]

        query_vector = self._fallback_vector(query)
        scores = []
        for chunk, vector in zip(self.chunks, self.chunk_vectors):
            score = _cosine_similarity(query_vector, vector)
            scores.append((chunk, score))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:top_k]

    @staticmethod
    def _query_instruction(text: str, is_query: bool) -> str:
        prefix = "Query" if is_query else "Passage"
        return "Instruct: semiconductors technology analysis retrieval.\n%s: %s" % (prefix, text)

    @staticmethod
    def _fallback_vector(text: str, dims: int = 256) -> List[float]:
        tokens = re.findall(r"[A-Za-z0-9가-힣\.\-]+", text.lower())
        vector = [0.0] * dims
        for token in tokens:
            index = sum(ord(ch) for ch in token) % dims
            vector[index] += 1.0
        return vector


class HybridRetriever:
    def __init__(self, chunks: Sequence[ChunkRecord], model_name: str):
        self.chunks = list(chunks)
        self.dense = E5CompatibleDenseRetriever(chunks, model_name=model_name)
        self.bm25 = BM25Retriever(chunks)

    def search(self, query: str, top_k: int = 4) -> List[EvidenceItem]:
        dense_results = self.dense.search(query, top_k=top_k * 2)
        sparse_results = self.bm25.search(query, top_k=top_k * 2)
        combined = {}
        for chunk, score in _normalize_scores(dense_results):
            combined.setdefault(chunk.chunk_id, [chunk, 0.0])[1] += score * 0.5
        for chunk, score in _normalize_scores(sparse_results):
            combined.setdefault(chunk.chunk_id, [chunk, 0.0])[1] += score * 0.5

        ranked = sorted(combined.values(), key=lambda item: item[1], reverse=True)[:top_k]
        evidence = []
        for chunk, score in ranked:
            confidence = "high" if score >= 0.66 else "medium" if score >= 0.33 else "low"
            evidence.append(
                EvidenceItem(
                    title=chunk.source_path.name,
                    content=chunk.text,
                    source_path=str(chunk.source_path),
                    source_type=chunk.source_type,
                    page=chunk.page,
                    published_at=chunk.published_at,
                    confidence=confidence,
                )
            )
        return evidence


class CorpusRegistry:
    def __init__(self, runtime: RuntimeConfig):
        self.runtime = runtime
        self._retrievers = {}

    def get_retriever(self, corpus_name: str) -> HybridRetriever:
        if corpus_name not in self._retrievers:
            base_dir = self.runtime.resolve_reference_dir(corpus_name)
            chunks = load_pdf_chunks(base_dir)
            self._retrievers[corpus_name] = HybridRetriever(
                chunks,
                model_name=self.runtime.embedding_model_name,
            )
        return self._retrievers[corpus_name]

    def search(self, corpus_name: str, query: str, top_k: int = 4) -> List[EvidenceItem]:
        return self.get_retriever(corpus_name).search(query=query, top_k=top_k)


def load_pdf_chunks(directory: Path, chunk_size: int = 900, overlap: int = 120) -> List[ChunkRecord]:
    chunks = []
    for path in sorted(directory.glob("*.pdf")):
        reader = PdfReader(str(path))
        source_type = infer_source_type(path.name)
        published_at = infer_publication_date(reader, path)
        for page_index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            normalized = _normalize_whitespace(text)
            if not normalized:
                continue
            for chunk_number, piece in enumerate(_chunk_text(normalized, chunk_size, overlap), start=1):
                chunks.append(
                    ChunkRecord(
                        chunk_id="%s:%s:%s" % (path.name, page_index, chunk_number),
                        text=piece,
                        source_path=path,
                        source_type=source_type,
                        page=page_index,
                        published_at=published_at,
                    )
                )
    return chunks


def infer_source_type(filename: str) -> str:
    lowered = filename.lower()
    if "표준" in filename or "standard" in lowered or "jedec" in lowered or "cxl" in lowered:
        return "standard"
    if "학술" in filename or "thesis" in lowered:
        return "paper"
    if "irds" in lowered:
        return "report"
    if "nasa" in lowered:
        return "report"
    return "paper"


def infer_publication_date(reader: PdfReader, path: Path) -> Optional[date]:
    metadata = reader.metadata or {}
    for key in ("/CreationDate", "/ModDate"):
        raw = metadata.get(key)
        parsed = _parse_pdf_date(raw)
        if parsed:
            return parsed
    first_page = reader.pages[0].extract_text() or ""
    match = re.search(r"\b(20[0-3][0-9])\b", first_page)
    if match:
        return date(int(match.group(1)), 1, 1)
    return date.fromtimestamp(path.stat().st_mtime)


def _parse_pdf_date(raw_value: Optional[str]) -> Optional[date]:
    if not raw_value:
        return None
    match = re.search(r"D:(\d{4})(\d{2})(\d{2})", raw_value)
    if not match:
        return None
    year, month, day = [int(part) for part in match.groups()]
    return date(year, month, day)


def _chunk_text(text: str, chunk_size: int, overlap: int) -> Iterable[str]:
    if len(text) <= chunk_size:
        yield text
        return
    start = 0
    step = chunk_size - overlap
    while start < len(text):
        yield text[start : start + chunk_size]
        start += step


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_scores(items: Sequence[Tuple[ChunkRecord, float]]) -> List[Tuple[ChunkRecord, float]]:
    if not items:
        return []
    raw_scores = [score for _, score in items]
    max_score = max(raw_scores)
    min_score = min(raw_scores)
    if math.isclose(max_score, min_score):
        return [(chunk, 1.0) for chunk, _ in items]
    normalized = []
    for chunk, score in items:
        normalized.append((chunk, (score - min_score) / (max_score - min_score)))
    return normalized


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)
