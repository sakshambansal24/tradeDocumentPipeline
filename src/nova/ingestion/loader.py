from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from nova.ingestion.errors import UnreadableDocumentError
from nova.ingestion.page_prep import (
    DEFAULT_DPI,
    load_image,
    prepare_page_image,
    rasterize_pdf_pages,
)
from nova.schemas.ingestion import LoadedDocument, PageImage

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"}
PDF_CONTENT_TYPES = {"application/pdf"}
IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/tiff",
}


@dataclass(frozen=True)
class DocumentInput:
    data: bytes
    source_filename: str | None
    detected_type: str


class DocumentLoader:
    def __init__(self, *, dpi: int = DEFAULT_DPI) -> None:
        if dpi <= 0:
            raise ValueError("dpi must be positive")
        self.dpi = dpi

    def load(
        self,
        source: str | Path | bytes,
        *,
        content_type: str | None = None,
        source_filename: str | None = None,
    ) -> LoadedDocument:
        document_input = self._read_input(
            source,
            content_type=content_type,
            source_filename=source_filename,
        )

        digest = sha256(document_input.data).hexdigest()
        pages = list(self._prepare_pages(document_input))

        if not pages:
            raise UnreadableDocumentError("Document produced no readable pages")

        return LoadedDocument(
            doc_id=digest[:16],
            source_filename=document_input.source_filename,
            page_count=len(pages),
            pages=pages,
            original_bytes_hash=digest,
        )

    def _read_input(
        self,
        source: str | Path | bytes,
        *,
        content_type: str | None,
        source_filename: str | None,
    ) -> DocumentInput:
        if isinstance(source, bytes):
            data = source
            filename = source_filename
        else:
            path = Path(source)
            filename = source_filename or path.name
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise UnreadableDocumentError(f"Document could not be read: {exc}") from exc

        if not data:
            raise UnreadableDocumentError("Document input is empty")

        detected_type = self._detect_type(
            data,
            content_type=content_type,
            source_filename=filename,
        )

        return DocumentInput(data=data, source_filename=filename, detected_type=detected_type)

    def _detect_type(
        self,
        data: bytes,
        *,
        content_type: str | None,
        source_filename: str | None,
    ) -> str:
        normalized_content_type = (
            content_type.lower().split(";")[0].strip() if content_type else None
        )

        if normalized_content_type in PDF_CONTENT_TYPES:
            return "pdf"
        if normalized_content_type in IMAGE_CONTENT_TYPES:
            return "image"

        if source_filename:
            extension = Path(source_filename).suffix.lower()
            if extension not in SUPPORTED_EXTENSIONS:
                raise UnreadableDocumentError(
                    f"Unsupported document extension: {extension or '<none>'}"
                )
            if extension == ".pdf":
                return "pdf"
            return "image"

        if data.startswith(b"%PDF"):
            return "pdf"
        if data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*")):
            return "image"

        raise UnreadableDocumentError("Could not detect document type")

    def _prepare_pages(self, document_input: DocumentInput) -> Iterable[PageImage]:
        if document_input.detected_type == "pdf":
            images = rasterize_pdf_pages(document_input.data, dpi=self.dpi)
        elif document_input.detected_type == "image":
            images = [load_image(document_input.data)]
        else:
            raise UnreadableDocumentError(
                f"Unsupported detected type: {document_input.detected_type}"
            )

        for index, image in enumerate(images, start=1):
            try:
                yield prepare_page_image(image, page_number=index, dpi=self.dpi)
            except UnreadableDocumentError:
                raise
            except Exception as exc:
                raise UnreadableDocumentError(f"Page {index} could not be prepared: {exc}") from exc
