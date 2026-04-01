"""
Flask Backend — Intelligent Document Q&A API
Wraps the RAG pipeline with REST endpoints and file upload support.
"""

import os
import logging
import time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, jsonify, render_template, send_from_directory
from werkzeug.utils import secure_filename

from rag_pipeline import get_pipeline, DOCS_DIR

# ── App Setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "rag-dev-secret-2024")

ALLOWED_EXTENSIONS = {"pdf", "txt"}
UPLOAD_FOLDER = Path(DOCS_DIR)
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def error_response(message: str, status: int = 400):
    return jsonify({"status": "error", "message": message}), status


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the main chat UI."""
    return render_template("index.html")


@app.route("/health")
def health():
    """Health check for Cloud Run / load balancer."""
    pipeline = get_pipeline()
    stats = pipeline.get_stats()
    return jsonify({
        "status": "healthy",
        "ready": stats["ready"],
        "vectors_indexed": stats["vectors"],
        "llm_provider": os.getenv("LLM_PROVIDER", "groq"),
    })


@app.route("/api/stats")
def stats():
    """Return current pipeline statistics."""
    pipeline = get_pipeline()
    s = pipeline.get_stats()
    docs = list(UPLOAD_FOLDER.glob("**/*.pdf")) + list(UPLOAD_FOLDER.glob("**/*.txt"))
    return jsonify({
        "status": "ok",
        "vectors_indexed": s["vectors"],
        "documents": [f.name for f in docs],
        "document_count": len(docs),
        "pipeline_ready": s["ready"],
    })


@app.route("/api/upload", methods=["POST"])
def upload_documents():
    """
    Accept one or more PDF/TXT files, save them, then trigger ingestion.
    """
    if "files" not in request.files:
        return error_response("No files part in request.")

    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        return error_response("No files selected.")

    saved = []
    skipped = []
    for f in files:
        if f and allowed_file(f.filename):
            filename = secure_filename(f.filename)
            dest = UPLOAD_FOLDER / filename
            f.save(dest)
            saved.append(filename)
            logger.info(f"Saved upload: {filename}")
        else:
            skipped.append(f.filename)

    if not saved:
        return error_response(f"No valid files uploaded. Accepted types: {ALLOWED_EXTENSIONS}")

    # Trigger ingestion on the freshly uploaded files
    pipeline = get_pipeline()
    result = pipeline.ingest(str(UPLOAD_FOLDER))

    return jsonify({
        "status": "success",
        "saved_files": saved,
        "skipped_files": skipped,
        "ingestion": result,
    })


@app.route("/api/ingest", methods=["POST"])
def ingest():
    """
    Manually trigger ingestion of all documents in the documents folder.
    Useful for re-indexing after adding files via other means.
    """
    pipeline = get_pipeline()
    result = pipeline.ingest(str(UPLOAD_FOLDER))
    return jsonify({"status": "ok", "result": result})


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Main Q&A endpoint.
    Request body: { "question": "..." }
    Response:     { "answer": "...", "sources": [...], "context_chunks": [...] }
    """
    body = request.get_json(force=True, silent=True)
    if not body or "question" not in body:
        return error_response("Request body must contain a 'question' field.")

    question = body["question"].strip()
    if not question:
        return error_response("Question cannot be empty.")
    if len(question) > 2000:
        return error_response("Question exceeds 2000 character limit.")

    logger.info(f"Query received: {question[:100]}...")

    t0 = time.time()
    try:
        pipeline = get_pipeline()
        result = pipeline.query(question)
        elapsed = round(time.time() - t0, 2)
        result["elapsed_seconds"] = elapsed
        result["status"] = "ok"
        logger.info(f"Query answered in {elapsed}s")
        return jsonify(result)
    except ValueError as e:
        logger.error(f"Config error: {e}")
        return error_response(str(e), 503)
    except Exception as e:
        logger.exception(f"Unexpected error during query: {e}")
        return error_response(f"Internal server error: {str(e)}", 500)


@app.route("/api/reset", methods=["POST"])
def reset():
    """Delete all indexed vectors (but keep uploaded files)."""
    import shutil
    chroma_dir = Path("./chroma_db")
    if chroma_dir.exists():
        shutil.rmtree(chroma_dir)

    # Reset singleton
    import rag_pipeline
    rag_pipeline._pipeline = None

    return jsonify({"status": "ok", "message": "Vector index cleared."})


# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    logger.info(f"Starting RAG server on port {port} | debug={debug}")
    app.run(host="0.0.0.0", port=port, debug=debug)