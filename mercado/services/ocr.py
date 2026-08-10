from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .parser import parse_receipt_text


def preprocess_receipt(path: str | Path) -> Image.Image:
    image = Image.open(path)
    image = ImageOps.exif_transpose(image).convert("L")
    max_width = 2200
    if image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, int(image.height * ratio)))
    image = ImageEnhance.Contrast(image).enhance(1.8)
    image = ImageEnhance.Sharpness(image).enhance(1.5)
    image = image.filter(ImageFilter.MedianFilter(size=3))
    return image


def extract_text(path: str | Path, language: str = "por") -> str:
    image = preprocess_receipt(path)
    attempts = []
    for psm in (6, 4):
        try:
            attempts.append(
                pytesseract.image_to_string(
                    image, lang=language, config=f"--oem 3 --psm {psm}"
                )
            )
        except pytesseract.TesseractError:
            attempts.append(
                pytesseract.image_to_string(
                    image, lang="eng", config=f"--oem 3 --psm {psm}"
                )
            )
    return max(attempts, key=lambda value: len(value.strip()), default="")


def decode_qr(path: str | Path) -> str | None:
    image = cv2.imread(str(path))
    if image is None:
        return None
    detector = cv2.QRCodeDetector()
    height, width = image.shape[:2]
    variants = [image]
    if max(height, width) > 1800:
        scale = 1800 / max(height, width)
        variants.append(
            cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        )
    gray = cv2.cvtColor(variants[-1], cv2.COLOR_BGR2GRAY)
    variants.extend(
        [
            gray,
            cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
            cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 7
            ),
        ]
    )
    for candidate in variants:
        try:
            ok, decoded, _points, _ = detector.detectAndDecodeMulti(candidate)
            if ok:
                for value in decoded:
                    if value:
                        return value.strip()
        except (cv2.error, ValueError):
            pass
        value, _points, _ = detector.detectAndDecode(candidate)
        if value:
            return value.strip()
        try:
            value, _points, _ = detector.detectAndDecodeCurved(candidate)
            if value:
                return value.strip()
        except cv2.error:
            pass
    return None


def process_image(path: str | Path, language: str = "por") -> dict:
    raw_text = extract_text(path, language)
    parsed = parse_receipt_text(raw_text)
    parsed["raw_text"] = raw_text
    parsed["qr_url"] = decode_qr(path) or parsed.get("qr_url")
    return parsed
