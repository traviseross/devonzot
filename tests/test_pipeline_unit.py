"""
Unit tests for pipeline helper functions.

Tests retry logic, text sanitization, author extraction, and other
utility functions used in the pipeline orchestration.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import sys
import time

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from pipeline_add_url import (
    retry_with_backoff,
    sanitize_short_title,
    author_surname_from_list,
    create_zotero_item_with_retry
)
from zotero_api_client import ZoteroAPIClient
from exceptions import ZoteroAPIError


class TestRetryWithBackoff:
    """Test suite for retry_with_backoff decorator."""

    def test_retry_with_backoff_success_first_attempt(self):
        """Test function succeeds on first attempt."""
        # Arrange
        @retry_with_backoff(max_attempts=3)
        def successful_function():
            return "success"

        # Act
        result = successful_function()

        # Assert
        assert result == "success"

    @patch('time.sleep')
    def test_retry_with_backoff_success_after_retries(self, mock_sleep):
        """Test function succeeds after failing twice."""
        # Arrange
        call_count = 0

        @retry_with_backoff(max_attempts=5, initial_delay=1.0, backoff_factor=2.0)
        def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Temporary error")
            return "success"

        # Act
        result = flaky_function()

        # Assert
        assert result == "success"
        assert call_count == 3
        # Should have slept twice (after 1st and 2nd failures)
        assert mock_sleep.call_count == 2
        # Verify exponential backoff: 1s, 2s
        assert mock_sleep.call_args_list[0][0][0] == 1.0
        assert mock_sleep.call_args_list[1][0][0] == 2.0

    @patch('time.sleep')
    def test_retry_with_backoff_max_attempts_exhausted(self, mock_sleep):
        """Test raises exception after max attempts exhausted."""
        # Arrange
        call_count = 0

        @retry_with_backoff(max_attempts=3, initial_delay=1.0)
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("Permanent error")

        # Act & Assert
        with pytest.raises(ValueError, match="Permanent error"):
            always_fails()

        assert call_count == 3
        assert mock_sleep.call_count == 2  # Sleeps after 1st and 2nd failure

    @patch('time.sleep')
    def test_retry_with_backoff_custom_parameters(self, mock_sleep):
        """Test retry with custom max_attempts and initial_delay."""
        # Arrange
        call_count = 0

        @retry_with_backoff(max_attempts=2, initial_delay=2.0, backoff_factor=3.0)
        def custom_retry_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Temporary error")
            return "success"

        # Act
        result = custom_retry_function()

        # Assert
        assert result == "success"
        assert call_count == 2
        assert mock_sleep.call_count == 1
        # Verify custom initial delay
        assert mock_sleep.call_args[0][0] == 2.0

    @patch('time.sleep')
    def test_create_zotero_item_with_retry_decorator_applied(self, mock_sleep):
        """Test retry decorator works with create_zotero_item_with_retry."""
        # Arrange
        mock_zot = Mock(spec=ZoteroAPIClient)
        call_count = 0

        def create_item_side_effect(url):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return None  # Fails first time
            return {'key': 'ABC123', 'data': {}}

        mock_zot.create_item_from_url = Mock(side_effect=create_item_side_effect)

        # Act
        result = create_zotero_item_with_retry(mock_zot, 'https://example.com')

        # Assert
        assert result['key'] == 'ABC123'
        assert call_count == 2
        assert mock_sleep.called


class TestSanitizeShortTitle:
    """Test suite for sanitize_short_title function."""

    def test_sanitize_short_title_normal(self):
        """Test sanitization of normal title."""
        # Arrange
        title = "Machine Learning in Practice"

        # Act
        result = sanitize_short_title(title)

        # Assert
        assert result == "Machine Learning in Practice"

    def test_sanitize_short_title_with_special_chars(self):
        """Test removal of special characters."""
        # Arrange
        title = 'Title: With <Special> Characters/Symbols|Test?'

        # Act
        result = sanitize_short_title(title)

        # Assert
        assert '<' not in result
        assert '>' not in result
        assert ':' not in result
        assert '/' not in result
        assert '|' not in result
        assert '?' not in result
        assert 'Title' in result
        assert 'Special' in result

    def test_sanitize_short_title_truncation(self):
        """Test truncation at word boundary."""
        # Arrange
        title = "This is a very long title that exceeds the sixty character limit and should be truncated at a word boundary"

        # Act
        result = sanitize_short_title(title, length=60)

        # Assert
        assert len(result) <= 63  # 60 + "..."
        assert result.endswith('...')
        assert ' ' not in result[-4:]  # No trailing space before ...

    def test_sanitize_short_title_empty_string(self):
        """Test handling of empty or None title."""
        # Act
        result1 = sanitize_short_title('')
        result2 = sanitize_short_title(None)

        # Assert
        assert result1 == "untitled"
        assert result2 == "untitled"

    def test_sanitize_short_title_whitespace_normalization(self):
        """Test normalization of multiple whitespace."""
        # Arrange
        title = "Title   with    multiple     spaces"

        # Act
        result = sanitize_short_title(title)

        # Assert
        assert "  " not in result
        assert result == "Title with multiple spaces"


class TestAuthorSurnameFromList:
    """Test suite for author_surname_from_list function."""

    def test_author_surname_from_list_single_author_dict(self):
        """Test extraction from single author dict."""
        # Arrange
        authors = [{"lastName": "Smith", "firstName": "John"}]

        # Act
        result = author_surname_from_list(authors)

        # Assert
        assert result == "Smith"

    def test_author_surname_from_list_with_last_key(self):
        """Test extraction using 'last' key instead of 'lastName'."""
        # Arrange
        authors = [{"last": "Doe", "first": "Jane"}]

        # Act
        result = author_surname_from_list(authors)

        # Assert
        assert result == "Doe"

    def test_author_surname_from_list_multiple_authors(self):
        """Test returns first author's surname when multiple authors."""
        # Arrange
        authors = [
            {"lastName": "Smith", "firstName": "John"},
            {"lastName": "Doe", "firstName": "Jane"},
            {"lastName": "Johnson", "firstName": "Bob"}
        ]

        # Act
        result = author_surname_from_list(authors)

        # Assert
        assert result == "Smith"  # First author only

    def test_author_surname_from_list_string_format(self):
        """Test extraction from string format (e.g., 'John Smith')."""
        # Arrange
        authors = ["John Smith"]

        # Act
        result = author_surname_from_list(authors)

        # Assert
        assert result == "Smith"

    def test_author_surname_from_list_empty(self):
        """Test handling of empty author list."""
        # Act
        result = author_surname_from_list([])

        # Assert
        assert result == ""

    def test_author_surname_from_list_none(self):
        """Test handling of None author list."""
        # Act
        result = author_surname_from_list(None)

        # Assert
        assert result == ""
