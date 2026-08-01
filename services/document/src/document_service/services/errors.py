class DocumentError(Exception):
    """Base error exposed by the transport-neutral document boundary."""


class DocumentConflictError(DocumentError):
    """Raised when a document cannot be created because it already exists."""


class DocumentNotFoundError(DocumentError):
    """Raised when a document or its generated artifact cannot be found."""


class DocumentExtractionUnavailableError(DocumentError):
    """Raised when draft extraction is temporarily unavailable."""


class DocumentExtractionInvalidError(DocumentError):
    """Raised when extracted draft data cannot be validated."""


class DocumentItemsUnavailableError(DocumentError):
    """Raised when raw item normalization is temporarily unavailable."""


class DocumentItemsInvalidError(DocumentError):
    """Raised when raw invoice items cannot be normalized."""
