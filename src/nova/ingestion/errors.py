class UnreadableDocumentError(Exception):
    """Raised when a document or page cannot be prepared for extraction."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
