"""
RAG Pipeline — Retrieval-Augmented Generation Core
Handles embedding, vector storage, retrieval, and LLM generation.
"""

import os
import logging
from typing import List, Dict, Optional, Tuple
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader, DirectoryLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain.schema import Document

import requests
import json

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────
CHUNK_SIZE        = 500
CHUNK_OVERLAP     = 50
TOP_K_RESULTS     = 5
EMBEDDING_MODEL   = "sentence-transformers/all-MiniLM-L6-v2"
CHROMA_PERSIST_DIR = "./chroma_db"
DOCS_DIR          = "./documents"

# Supported LLM providers — set via env vars
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
LLM_PROVIDER    = os.getenv("LLM_PROVIDER", "groq")   # "groq" | "gemini"

GROQ_MODEL      = "llama-3.1-8b-instant"
GEMINI_MODEL    = "gemini-2.0-flash"

SYSTEM_PROMPT_TEMPLATE = """You are a specialized document intelligence assistant. \
Your sole purpose is to answer questions based strictly on the provided context extracted \
from the user's documents.

Rules you must follow without exception:
1. Answer ONLY using information found in the context below.
2. If the answer is not explicitly contained within the context, respond exactly with: \
"I do not have enough information in the provided documents to answer this question."
3. Never fabricate facts, statistics, dates, or names.
4. When citing information, reference the source document and page number if available.
5. Be concise, precise, and professional.

Context from documents:
{context}

User Question: {question}

Answer:"""


# ── Embedding & Vector Store ───────────────────────────────────────────────────

class EmbeddingStore:
    """Manages HuggingFace embeddings and ChromaDB persistence."""

    def __init__(self):
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        self.embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        self.vectorstore: Optional[Chroma] = None
        self._load_existing_store()

    def _load_existing_store(self):
        """Load ChromaDB if it already exists on disk."""
        if Path(CHROMA_PERSIST_DIR).exists():
            try:
                self.vectorstore = Chroma(
                    persist_directory=CHROMA_PERSIST_DIR,
                    embedding_function=self.embeddings,
                )
                count = self.vectorstore._collection.count()
                logger.info(f"Loaded existing ChromaDB with {count} vectors.")
            except Exception as e:
                logger.warning(f"Could not load existing ChromaDB: {e}")

    def ingest_documents(self, docs_dir: str = DOCS_DIR) -> Dict:
        """Load PDFs → chunk → embed → store. Returns ingestion stats."""
        docs_path = Path(docs_dir)
        if not docs_path.exists():
            docs_path.mkdir(parents=True)

        # Discover PDF files
        pdf_files = list(docs_path.glob("**/*.pdf"))
        txt_files = list(docs_path.glob("**/*.txt"))
        all_files = pdf_files + txt_files

        if not all_files:
            return {"status": "error", "message": f"No PDF or TXT files found in '{docs_dir}'"}

        raw_documents: List[Document] = []

        # Load PDFs
        for pdf_path in pdf_files:
            try:
                loader = PyPDFLoader(str(pdf_path))
                pages  = loader.load()
                raw_documents.extend(pages)
                logger.info(f"Loaded PDF: {pdf_path.name} ({len(pages)} pages)")
            except Exception as e:
                logger.error(f"Failed to load {pdf_path.name}: {e}")

        # Load plain-text files
        for txt_path in txt_files:
            try:
                from langchain_community.document_loaders import TextLoader
                loader = TextLoader(str(txt_path), encoding="utf-8")
                docs   = loader.load()
                raw_documents.extend(docs)
                logger.info(f"Loaded TXT: {txt_path.name} ({len(docs)} sections)")
            except Exception as e:
                logger.error(f"Failed to load {txt_path.name}: {e}")

        if not raw_documents:
            return {"status": "error", "message": "No content could be extracted from documents."}

        # Chunk
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        chunks = splitter.split_documents(raw_documents)
        logger.info(f"Created {len(chunks)} chunks from {len(raw_documents)} pages.")

        # Embed & persist
        self.vectorstore = Chroma.from_documents(
            documents=chunks,
            embedding=self.embeddings,
            persist_directory=CHROMA_PERSIST_DIR,
        )
        self.vectorstore.persist()
        logger.info("Vectors persisted to ChromaDB.")

        return {
            "status": "success",
            "files_loaded": len(all_files),
            "pages_extracted": len(raw_documents),
            "chunks_created": len(chunks),
            "vectors_stored": len(chunks),
        }

    def similarity_search(self, query: str, k: int = TOP_K_RESULTS) -> List[Document]:
        """Return top-k semantically similar chunks for a query."""
        if self.vectorstore is None:
            raise RuntimeError("Vector store is empty. Please ingest documents first.")
        return self.vectorstore.similarity_search(query, k=k)

    def is_ready(self) -> bool:
        if self.vectorstore is None:
            return False
        return self.vectorstore._collection.count() > 0

    def get_stats(self) -> Dict:
        if self.vectorstore is None:
            return {"vectors": 0, "ready": False}
        count = self.vectorstore._collection.count()
        return {"vectors": count, "ready": count > 0}


# ── LLM Clients ───────────────────────────────────────────────────────────────

def _call_groq(prompt: str) -> str:
    """Call Groq inference API (Llama 3.1)."""
    # Truncate prompt to stay within the context window (~8k tokens)
    MAX_PROMPT_CHARS = 12000
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS] + "\n\n[Context truncated to fit model limits]"

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 1024,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"].strip()


def _call_gemini(prompt: str) -> str:
    """Call Google Gemini API with exponential backoff on 429."""
    import time as _time
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024},
    }
    for attempt in range(4):          # up to 4 tries: 0, 5, 15, 45 seconds
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 429:
            wait = 5 * (3 ** attempt)  # 5s, 15s, 45s
            logger.warning(f"Gemini rate limited. Retrying in {wait}s… (attempt {attempt+1})")
            _time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    raise RuntimeError("Gemini rate limit exceeded after retries. Try again in a minute.")


def call_llm(prompt: str) -> str:
    """Route to the configured LLM provider."""
    provider = LLM_PROVIDER.lower()
    if provider == "groq":
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY environment variable not set.")
        return _call_groq(prompt)
    elif provider == "gemini":
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY environment variable not set.")
        return _call_gemini(prompt)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: '{provider}'. Use 'groq' or 'gemini'.")


# ── RAG Pipeline ──────────────────────────────────────────────────────────────

class RAGPipeline:
    """End-to-end RAG: retrieve context → build prompt → generate answer."""

    def __init__(self):
        self.store = EmbeddingStore()

    def ingest(self, docs_dir: str = DOCS_DIR) -> Dict:
        return self.store.ingest_documents(docs_dir)

    def query(self, question: str) -> Dict:
        """
        Full RAG query cycle.
        Returns: { answer, sources, context_chunks, question }
        """
        if not self.store.is_ready():
            return {
                "answer": "No documents have been ingested yet. Please upload documents first.",
                "sources": [],
                "context_chunks": [],
                "question": question,
            }

        # Step 1 — Retrieve relevant chunks
        retrieved: List[Document] = self.store.similarity_search(question, k=TOP_K_RESULTS)

        if not retrieved:
            return {
                "answer": "I do not have enough information in the provided documents to answer this question.",
                "sources": [],
                "context_chunks": [],
                "question": question,
            }

        # Step 2 — Build context string
        context_parts = []
        sources = []
        for i, doc in enumerate(retrieved, 1):
            meta   = doc.metadata
            source = meta.get("source", "Unknown")
            page   = meta.get("page", "?")
            source_label = f"{Path(source).name}, page {page}"
            context_parts.append(f"[Source {i}: {source_label}]\n{doc.page_content}")
            if source_label not in sources:
                sources.append(source_label)

        context = "\n\n---\n\n".join(context_parts)

        # Step 3 — Build engineered prompt
        prompt = SYSTEM_PROMPT_TEMPLATE.format(context=context, question=question)

        # Step 4 — LLM generation
        answer = call_llm(prompt)

        return {
            "answer": answer,
            "sources": sources,
            "context_chunks": [
                {"content": doc.page_content[:300] + "...", "metadata": doc.metadata}
                for doc in retrieved
            ],
            "question": question,
        }

    def is_ready(self) -> bool:
        return self.store.is_ready()

    def get_stats(self) -> Dict:
        return self.store.get_stats()


# ── Singleton ─────────────────────────────────────────────────────────────────
_pipeline: Optional[RAGPipeline] = None

def get_pipeline() -> RAGPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = RAGPipeline()
    return _pipeline