from __future__ import annotations

import logging
import time
from pathlib import Path

from . import create_app
from .db import get_db
from .routes import enrich_items_from_catalog
from .services.ocr import process_image
from .services.receipt_jobs import claim_next_receipt, fail_receipt, finalize_receipt


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("mercado.worker")


def process_one(app) -> bool:
    with app.app_context():
        job = claim_next_receipt(
            get_db(), int(app.config.get("PROCESSING_STALE_MINUTES", 20))
        )
    if not job:
        return False

    receipt_id = int(job["id"])
    image_path = Path(app.config["UPLOAD_FOLDER"]) / job["image_path"]
    LOGGER.info("Processando recibo %s com modo %s", receipt_id, job["ocr_mode"])
    try:
        parsed = process_image(
            image_path,
            app.config["TESSERACT_LANG"],
            mode=job["ocr_mode"] or app.config["OCR_PROVIDER"],
            llm_settings=app.config,
        )
        with app.app_context():
            enrich_items_from_catalog(parsed)
            finalize_receipt(get_db(), receipt_id, parsed)
        LOGGER.info("Recibo %s processado", receipt_id)
    except Exception as error:  # o worker precisa persistir qualquer falha e continuar
        LOGGER.exception("Falha ao processar recibo %s", receipt_id)
        with app.app_context():
            fail_receipt(get_db(), receipt_id, error)
    return True


def main() -> None:
    app = create_app({"SKIP_SEED": True})
    poll_seconds = max(0.2, float(app.config.get("WORKER_POLL_SECONDS", 1)))
    LOGGER.info("Worker de cupons iniciado")
    while True:
        if not process_one(app):
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
