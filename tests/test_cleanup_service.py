"""
Unit tests for cleanup service module.

Tests TempFileManager context manager and cleanup utilities.
"""

import pytest
import time
from pathlib import Path
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from cleanup_service import TempFileManager, cleanup_old_tmp_files


class TestTempFileManager:
    """Test TempFileManager context manager."""

    @pytest.fixture
    def tmp_test_dir(self, tmp_path):
        """Create a temporary test directory."""
        test_dir = tmp_path / 'test_tmp'
        test_dir.mkdir()
        return test_dir

    def test_init_creates_tmp_dir(self, tmp_path):
        """Test that initialization creates tmp directory."""
        tmp_dir = tmp_path / 'new_tmp'
        assert not tmp_dir.exists()

        with TempFileManager(tmp_dir=tmp_dir):
            assert tmp_dir.exists()

    def test_register_file(self, tmp_test_dir):
        """Test registering a file for tracking."""
        with TempFileManager(tmp_dir=tmp_test_dir) as tmpfiles:
            file_path = tmpfiles.register(tmp_test_dir / 'test.txt')

            assert file_path in tmpfiles.tracked_files
            assert isinstance(file_path, Path)

    def test_cleanup_on_success(self, tmp_test_dir):
        """Test files are cleaned up when marked as success."""
        test_file = tmp_test_dir / 'test.txt'

        with TempFileManager(tmp_dir=tmp_test_dir, debug_mode=False) as tmpfiles:
            tmpfiles.register(test_file)
            test_file.write_text('test content')
            tmpfiles.mark_success()

        # File should be removed after success
        assert not test_file.exists()

    def test_cleanup_on_failure_without_debug(self, tmp_test_dir):
        """Test files are cleaned up on failure when debug_mode=False."""
        test_file = tmp_test_dir / 'test.txt'

        with TempFileManager(tmp_dir=tmp_test_dir, debug_mode=False) as tmpfiles:
            tmpfiles.register(test_file)
            test_file.write_text('test content')
            # Don't mark success - simulates failure

        # File should still be removed when debug_mode=False
        assert not test_file.exists()

    def test_no_cleanup_on_failure_with_debug(self, tmp_test_dir):
        """Test files are retained on failure when debug_mode=True."""
        test_file = tmp_test_dir / 'test.txt'

        with TempFileManager(tmp_dir=tmp_test_dir, debug_mode=True) as tmpfiles:
            tmpfiles.register(test_file)
            test_file.write_text('test content')
            # Don't mark success - simulates failure

        # File should be retained when debug_mode=True
        assert test_file.exists()
        assert test_file.read_text() == 'test content'

    def test_cleanup_multiple_files(self, tmp_test_dir):
        """Test cleaning up multiple tracked files."""
        files = [
            tmp_test_dir / 'test1.txt',
            tmp_test_dir / 'test2.txt',
            tmp_test_dir / 'test3.txt'
        ]

        with TempFileManager(tmp_dir=tmp_test_dir, debug_mode=False) as tmpfiles:
            for file in files:
                tmpfiles.register(file)
                file.write_text('content')
            tmpfiles.mark_success()

        # All files should be removed
        for file in files:
            assert not file.exists()

    def test_cleanup_handles_missing_files(self, tmp_test_dir):
        """Test cleanup handles files that don't exist gracefully."""
        test_file = tmp_test_dir / 'nonexistent.txt'

        # Should not raise error
        with TempFileManager(tmp_dir=tmp_test_dir, debug_mode=False) as tmpfiles:
            tmpfiles.register(test_file)
            tmpfiles.mark_success()

    def test_exception_propagates(self, tmp_test_dir):
        """Test that exceptions are not suppressed."""
        with pytest.raises(ValueError):
            with TempFileManager(tmp_dir=tmp_test_dir) as tmpfiles:
                raise ValueError("Test error")

    def test_cleanup_on_exception(self, tmp_test_dir):
        """Test cleanup behavior when exception occurs."""
        test_file = tmp_test_dir / 'test.txt'

        with pytest.raises(ValueError):
            with TempFileManager(tmp_dir=tmp_test_dir, debug_mode=False) as tmpfiles:
                tmpfiles.register(test_file)
                test_file.write_text('content')
                raise ValueError("Test error")

        # File should be cleaned up even on exception (debug_mode=False)
        assert not test_file.exists()


class TestCleanupOldFiles:
    """Test cleanup_old_files utility function."""

    @pytest.fixture
    def tmp_test_dir(self, tmp_path):
        """Create a temporary test directory with files of various ages."""
        test_dir = tmp_path / 'cleanup_test'
        test_dir.mkdir()
        return test_dir

    def test_cleanup_old_files(self, tmp_test_dir):
        """Test cleaning up files older than specified days."""
        # Create an old file
        old_file = tmp_test_dir / 'old_file.txt'
        old_file.write_text('old content')

        # Set modification time to 10 days ago
        old_time = time.time() - (10 * 86400)
        import os
        os.utime(old_file, (old_time, old_time))

        # Create a recent file
        recent_file = tmp_test_dir / 'recent_file.txt'
        recent_file.write_text('recent content')

        # Clean up files older than 7 days
        removed = TempFileManager.cleanup_old_files(tmp_test_dir, days=7)

        assert removed == 1
        assert not old_file.exists()      # Old file removed
        assert recent_file.exists()        # Recent file kept

    def test_cleanup_nonexistent_directory(self, tmp_path):
        """Test cleanup of non-existent directory returns 0."""
        nonexistent = tmp_path / 'nonexistent'
        removed = TempFileManager.cleanup_old_files(nonexistent, days=7)
        assert removed == 0

    def test_cleanup_empty_directory(self, tmp_test_dir):
        """Test cleanup of empty directory returns 0."""
        removed = TempFileManager.cleanup_old_files(tmp_test_dir, days=7)
        assert removed == 0

    def test_cleanup_old_tmp_files_convenience_function(self, tmp_test_dir):
        """Test the convenience function cleanup_old_tmp_files."""
        # Create an old file
        old_file = tmp_test_dir / 'old.txt'
        old_file.write_text('content')

        old_time = time.time() - (10 * 86400)
        import os
        os.utime(old_file, (old_time, old_time))

        removed = cleanup_old_tmp_files(tmp_dir=tmp_test_dir, retention_days=7)

        assert removed == 1
        assert not old_file.exists()


class TestTempFileManagerIntegration:
    """Integration tests for TempFileManager."""

    @pytest.fixture
    def tmp_test_dir(self, tmp_path):
        """Create a temporary test directory."""
        test_dir = tmp_path / 'integration_test'
        test_dir.mkdir()
        return test_dir

    def test_realistic_workflow_success(self, tmp_test_dir):
        """Test realistic workflow: create temp file, process, cleanup on success."""
        with TempFileManager(tmp_dir=tmp_test_dir, debug_mode=False) as tmpfiles:
            # Simulate pipeline creating a temp file
            output_file = tmpfiles.register(tmp_test_dir / 'article_extract.md')
            output_file.write_text('# Article\n\nContent here')

            # Simulate successful processing
            assert output_file.exists()
            content = output_file.read_text()
            assert '# Article' in content

            # Mark success
            tmpfiles.mark_success()

        # Temp file should be cleaned up
        assert not output_file.exists()

    def test_realistic_workflow_failure_debug(self, tmp_test_dir):
        """Test realistic workflow: failure in debug mode retains files."""
        output_file = tmp_test_dir / 'article_extract.md'

        try:
            with TempFileManager(tmp_dir=tmp_test_dir, debug_mode=True) as tmpfiles:
                tmpfiles.register(output_file)
                output_file.write_text('# Article\n\nContent')

                # Simulate failure
                raise Exception("Processing failed")

        except Exception:
            pass

        # File should be retained for debugging
        assert output_file.exists()
        assert '# Article' in output_file.read_text()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
