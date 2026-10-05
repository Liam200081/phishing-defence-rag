"""Explicit SentenceTransformer embeddings for both Chroma add and query."""
import uuid
from .ingest import digest


class ChromaRetriever:
    def __init__(self, chunks, config, encoder=None, client=None, resolved_revision=None):
        if not chunks:
            raise ValueError("Cannot index empty corpus")
        if encoder is None:
            from huggingface_hub import model_info
            from sentence_transformers import SentenceTransformer
            resolved_revision = model_info(config["embedding_model"], revision=config["embedding_revision"]).sha
            encoder = SentenceTransformer(config["embedding_model"], revision=resolved_revision)
        if client is None:
            import chromadb
            client = chromadb.EphemeralClient()
        self.encoder = encoder
        self.client = client
        self.embedding = {"model": config["embedding_model"], "requested_revision": config["embedding_revision"],
                          "resolved_revision": resolved_revision or "unavailable", "normalize_embeddings": False}
        self.index_version = digest({"chunks": chunks, "embedding": self.embedding,
                                     "distance_metric": config["distance_metric"]})
        self.collection = client.create_collection(
            name="b1-" + uuid.uuid4().hex, embedding_function=None,
            metadata={"hnsw:space": config["distance_metric"]})
        self.chunks = {c["chunk_id"]: {**c, "index_version": self.index_version} for c in chunks}
        # Bound insert batches for ordinary Chroma limits without changing ranking.
        for start in range(0, len(chunks), 128):
            batch = chunks[start:start + 128]
            vectors = self.encode([c["text"] for c in batch])
            self.collection.add(ids=[c["chunk_id"] for c in batch],
                                documents=[c["text"] for c in batch], embeddings=vectors,
                                metadatas=[{k: v for k, v in self.chunks[c["chunk_id"]].items() if k != "text"} for c in batch])

    def encode(self, texts):
        vectors = self.encoder.encode(texts, normalize_embeddings=False, show_progress_bar=False)
        return vectors.tolist() if hasattr(vectors, "tolist") else vectors

    def retrieve(self, query, top_k):
        if top_k < 1:
            raise ValueError("top_k must be positive")
        result = self.collection.query(query_embeddings=self.encode([query]),
                                       n_results=min(top_k, len(self.chunks)), include=["distances"])
        ranked = sorted(zip(result["ids"][0], result["distances"][0]), key=lambda pair: (pair[1], pair[0]))
        return [{**self.chunks[cid], "score": float(distance), "score_kind": "cosine_distance_lower_is_better"}
                for cid, distance in ranked]

    def close(self):
        self.client.delete_collection(self.collection.name)
