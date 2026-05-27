class OGParseError(Exception):
    """Raised when the parser encounters malformed ObjectGraph syntax."""
    pass

class OGValidationError(Exception):
    """Raised during Stage 3 Verification if the generated .og file violates accuracy thresholds."""
    pass
