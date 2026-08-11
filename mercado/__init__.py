from __future__ import annotations

import os
from pathlib import Path

from flask import Flask

from .db import get_db, init_app, init_db
from .routes import bp
from .services.seed import seed_legacy_data


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    data_dir = Path(os.getenv("APP_DATA_DIR", Path(app.root_path).parent / "data"))
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-me"),
        DATABASE=str(data_dir / "mercado.db"),
        UPLOAD_FOLDER=str(data_dir / "uploads"),
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "15")) * 1024 * 1024,
        TESSERACT_LANG=os.getenv("TESSERACT_LANG", "por"),
        OCR_PROVIDER=os.getenv("OCR_PROVIDER", "hybrid").lower(),
        OCR_CONFIDENCE_THRESHOLD=float(os.getenv("OCR_CONFIDENCE_THRESHOLD", "0.75")),
        LLM_PROVIDER=os.getenv("LLM_PROVIDER", "openai").lower(),
        LLM_TIMEOUT_SECONDS=int(os.getenv("LLM_TIMEOUT_SECONDS", "300")),
        LLM_IMAGE_DETAIL=os.getenv("LLM_IMAGE_DETAIL", "high"),
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        OPENAI_MODEL=os.getenv("OPENAI_MODEL", "gpt-5.6-terra"),
        OPENAI_BASE_URL=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        OLLAMA_BASE_URL=os.getenv(
            "OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1"
        ),
        OLLAMA_MODEL=os.getenv("OLLAMA_MODEL", "qwen3-vl:8b"),
        OLLAMA_API_KEY=os.getenv("OLLAMA_API_KEY", "ollama"),
        OLLAMA_CONTEXT_LENGTH=int(os.getenv("OLLAMA_CONTEXT_LENGTH", "16384")),
        WORKER_POLL_SECONDS=float(os.getenv("WORKER_POLL_SECONDS", "1")),
        PROCESSING_STALE_MINUTES=int(os.getenv("PROCESSING_STALE_MINUTES", "20")),
        SKIP_SEED=False,
        NFC_FETCH_ENABLED=os.getenv("NFC_FETCH_ENABLED", "false").lower() == "true",
        NFC_ALLOWED_HOSTS=[
            value.strip().lower()
            for value in os.getenv(
                "NFC_ALLOWED_HOSTS", "dfe.ms.gov.br,sefaz.ms.gov.br,nfce.sefaz.ms.gov.br"
            ).split(",")
            if value.strip()
        ],
    )
    if test_config:
        app.config.update(test_config)

    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    init_app(app)
    app.register_blueprint(bp)

    with app.app_context():
        init_db()
        if not app.config.get("SKIP_SEED"):
            seed_path = Path(app.root_path).parent / "data" / "seed_compras.json"
            seed_legacy_data(get_db(), seed_path)

    return app
