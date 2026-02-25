"""
Custom exception hierarchy for DEVONzot pipeline.

Provides structured exception handling for different failure modes:
- ZoteroAPIError: Zotero API communication failures
- ArticleExtractionError: Content extraction failures
- DEVONthinkIntegrationError: DEVONthink operation failures
- NetworkError: Network connectivity issues
- TimeoutError: Operation timeout errors
"""


class DEVONzotError(Exception):
    """Base exception for all DEVONzot errors."""
    pass


class ZoteroAPIError(DEVONzotError):
    """Raised when Zotero API operations fail."""

    def __init__(self, message: str, status_code: int = None, response_body: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class ArticleExtractionError(DEVONzotError):
    """Raised when article content extraction fails."""

    def __init__(self, message: str, url: str = None, engine: str = None):
        super().__init__(message)
        self.url = url
        self.engine = engine


class DEVONthinkIntegrationError(DEVONzotError):
    """Raised when DEVONthink operations fail."""

    def __init__(self, message: str, operation: str = None):
        super().__init__(message)
        self.operation = operation


class NetworkError(DEVONzotError):
    """Raised when network operations fail."""

    def __init__(self, message: str, url: str = None):
        super().__init__(message)
        self.url = url


class TimeoutError(DEVONzotError):
    """Raised when operations exceed timeout limits."""

    def __init__(self, message: str, timeout_seconds: int = None, operation: str = None):
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.operation = operation
