import base64
from io import BytesIO

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

from nova.ingestion.errors import UnreadableDocumentError
from nova.schemas.ingestion import PageImage

DEFAULT_DPI = 200
MIN_LONG_EDGE = 900
MAX_LONG_EDGE = 2400
LOW_QUALITY_THRESHOLD = 0.35


def rasterize_pdf_pages(pdf_bytes: bytes, *, dpi: int = DEFAULT_DPI) -> list[Image.Image]:
    if not pdf_bytes:
        raise UnreadableDocumentError("PDF input is empty")

    try:
        document = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:
        raise UnreadableDocumentError(f"PDF could not be opened: {exc}") from exc

    if len(document) == 0:
        raise UnreadableDocumentError("PDF has no pages")

    pages: list[Image.Image] = []
    scale = dpi / 72

    try:
        for page_index in range(len(document)):
            try:
                page = document[page_index]
                # pypdfium2 bundles PDFium, so this avoids Poppler installs required by pdf2image.
                bitmap = page.render(scale=scale)
                pages.append(bitmap.to_pil())
            except Exception as exc:
                raise UnreadableDocumentError(
                    f"PDF page {page_index + 1} could not be rasterized: {exc}"
                ) from exc
    finally:
        document.close()

    return pages


def load_image(image_bytes: bytes) -> Image.Image:
    if not image_bytes:
        raise UnreadableDocumentError("Image input is empty")

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            return image.copy()
    except (UnidentifiedImageError, OSError) as exc:
        raise UnreadableDocumentError(f"Image could not be opened: {exc}") from exc


def prepare_page_image(
    image: Image.Image,
    *,
    page_number: int,
    dpi: int = DEFAULT_DPI,
) -> PageImage:
    warnings: list[str] = []
    prepared, rotation_applied_deg = normalize_image(image, warnings=warnings)
    quality_score = compute_quality_score(prepared)

    if quality_score < LOW_QUALITY_THRESHOLD:
        warnings.append("low_quality_page")

    return PageImage(
        page_number=page_number,
        width=prepared.width,
        height=prepared.height,
        dpi=dpi,
        base64_image=encode_image_base64(prepared),
        quality_score=quality_score,
        rotation_applied_deg=rotation_applied_deg,
        warnings=warnings,
    )


def normalize_image(image: Image.Image, *, warnings: list[str]) -> tuple[Image.Image, int]:
    exif_transposed = ImageOps.exif_transpose(image)
    rotation_applied_deg = 0

    if exif_transposed.size != image.size:
        rotation_applied_deg = 90 if exif_transposed.height > exif_transposed.width else 270
        warnings.append("exif_orientation_applied")

    normalized = exif_transposed.convert("RGB")

    if normalized.width > normalized.height * 1.15:
        normalized = normalized.rotate(90, expand=True)
        rotation_applied_deg = (rotation_applied_deg + 90) % 360
        warnings.append("landscape_page_rotated_to_portrait")

    normalized = resize_to_llm_friendly_bounds(normalized, warnings=warnings)
    return normalized, rotation_applied_deg


def resize_to_llm_friendly_bounds(image: Image.Image, *, warnings: list[str]) -> Image.Image:
    long_edge = max(image.size)

    if long_edge > MAX_LONG_EDGE:
        scale = MAX_LONG_EDGE / long_edge
        warnings.append("image_downscaled_for_llm")
    elif long_edge < MIN_LONG_EDGE:
        scale = MIN_LONG_EDGE / long_edge
        warnings.append("image_upscaled_for_llm")
    else:
        return image

    new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def encode_image_base64(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def compute_quality_score(image: Image.Image) -> float:
    grayscale = np.asarray(image.convert("L"), dtype=np.float32)
    if grayscale.size == 0:
        return 0.0

    sharpness = _laplacian_variance(grayscale)
    contrast = float(grayscale.std())
    megapixels = grayscale.size / 1_000_000

    sharpness_score = min(sharpness / 800.0, 1.0)
    contrast_score = min(contrast / 64.0, 1.0)
    resolution_score = min(megapixels / 1.5, 1.0)

    score = (0.5 * sharpness_score) + (0.3 * contrast_score) + (0.2 * resolution_score)
    return round(float(max(0.0, min(score, 1.0))), 4)


def _laplacian_variance(grayscale: np.ndarray) -> float:
    padded = np.pad(grayscale, 1, mode="edge")
    laplacian = (
        -4 * padded[1:-1, 1:-1]
        + padded[:-2, 1:-1]
        + padded[2:, 1:-1]
        + padded[1:-1, :-2]
        + padded[1:-1, 2:]
    )
    return float(laplacian.var())
