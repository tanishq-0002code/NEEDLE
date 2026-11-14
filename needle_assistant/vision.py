from __future__ import annotations

import io
from typing import Any

from PIL import Image


def _winrt_ocr(image: Image.Image) -> str:
    import winrt.windows.graphics.imaging as imaging
    import winrt.windows.media.ocr as ocr
    import winrt.windows.storage.streams as streams

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    payload = buffer.getvalue()

    stream = streams.InMemoryRandomAccessStream()
    writer = streams.DataWriter(stream)
    writer.write_bytes(payload)
    writer.store_async().get()
    stream.seek(0)

    decoder = imaging.BitmapDecoder.create_async(stream).get()
    frame = decoder.get_frame_async(0).get() if hasattr(decoder, "get_frame_async") else decoder.frame
    software_bitmap = (
        frame.get_software_bitmap_async().get()
        if hasattr(frame, "get_software_bitmap_async")
        else frame.software_bitmap
    )

    engine = ocr.OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        languages = getattr(ocr.OcrEngine, "available_recognizer_languages", None)
        if languages:
            try:
                engine = ocr.OcrEngine.try_create_from_language(languages[0])
            except Exception:
                engine = None
    if engine is None:
        raise RuntimeError("Windows OCR is not available for the current language.")

    result = engine.recognize_async(software_bitmap).get()
    return str(getattr(result, "text", "") or "")


def _winrt_ocr_words(image: Image.Image) -> list[dict[str, Any]]:
    import winrt.windows.graphics.imaging as imaging
    import winrt.windows.media.ocr as ocr
    import winrt.windows.storage.streams as streams

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    payload = buffer.getvalue()

    stream = streams.InMemoryRandomAccessStream()
    writer = streams.DataWriter(stream)
    writer.write_bytes(payload)
    writer.store_async().get()
    stream.seek(0)

    decoder = imaging.BitmapDecoder.create_async(stream).get()
    frame = decoder.get_frame_async(0).get() if hasattr(decoder, "get_frame_async") else decoder.frame
    software_bitmap = (
        frame.get_software_bitmap_async().get()
        if hasattr(frame, "get_software_bitmap_async")
        else frame.software_bitmap
    )

    engine = ocr.OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        languages = getattr(ocr.OcrEngine, "available_recognizer_languages", None)
        if languages:
            try:
                engine = ocr.OcrEngine.try_create_from_language(languages[0])
            except Exception:
                engine = None
    if engine is None:
        raise RuntimeError("Windows OCR is not available for the current language.")

    result = engine.recognize_async(software_bitmap).get()
    words: list[dict[str, Any]] = []
    try:
        lines = result.lines
    except Exception:
        lines = []
    for line in lines:
        try:
            line_words = line.words
        except Exception:
            continue
        for word in line_words:
            try:
                rect = word.bounding_rect
                words.append(
                    {
                        "text": str(word.text or ""),
                        "x": float(getattr(rect, "x", 0) or 0),
                        "y": float(getattr(rect, "y", 0) or 0),
                        "width": float(getattr(rect, "width", 0) or 0),
                        "height": float(getattr(rect, "height", 0) or 0),
                    }
                )
            except Exception:
                continue
    return words


def ocr_words(image: Any) -> list[dict[str, Any]]:
    if not isinstance(image, Image.Image):
        raise TypeError("ocr_words expects a PIL.Image.Image")
    return _winrt_ocr_words(image)


def ocr_image(image: Any) -> str:
    """Return recognized text from a PIL image using offline Windows OCR."""
    if not isinstance(image, Image.Image):
        raise TypeError("ocr_image expects a PIL.Image.Image")
    try:
        return _winrt_ocr(image)
    except Exception as exc:  # pragma: no cover - exercised on real Windows
        raise RuntimeError(f"Windows OCR failed: {exc}") from exc


def crop_to_rect(image: Image.Image, rect: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = rect
    width, height = image.size
    left = max(0, min(width, left))
    top = max(0, min(height, top))
    right = max(left, min(width, right))
    bottom = max(top, min(height, bottom))
    if right - left < 4 or bottom - top < 4:
        return image
    return image.crop((left, top, right, bottom))
