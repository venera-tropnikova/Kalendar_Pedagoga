"""Stage A: evidence-only DOC/DOCX extraction; no application integration."""
from .formats import ConversionError, ExtractionError, LibreOfficeConverter, detect_format
from .models import ExtractedDocument, SourceSpan
from .reader import extract_bytes, extract_document

__all__ = ["ConversionError", "ExtractionError", "LibreOfficeConverter", "detect_format",
           "ExtractedDocument", "SourceSpan", "extract_bytes", "extract_document"]
