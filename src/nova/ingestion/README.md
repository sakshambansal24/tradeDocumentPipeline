Ingestion converts raw PDFs/images into `LoadedDocument` objects for the Extractor Agent.
Each page is rasterized or normalized into an RGB image, resized for vision LLM input, and encoded as base64 PNG.
`PageImage.quality_score` and `warnings` must be checked downstream before trusting extraction confidence.
This layer does not OCR or extract fields; it only prepares pixels and surfaces image-quality risk.
Unreadable documents or pages raise `UnreadableDocumentError` instead of returning partial silent success.
