"""
Temporary file cleanup service for DEVONzot pipeline.

Provides automatic cleanup of temporary files with configurable retention
for debugging purposes.
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional


logger = logging.getLogger(__name__)


class TempFileManager:
    """
    Context manager for tracking and cleaning up temporary files.

    Usage:
        with TempFileManager(debug_mode=False) as tmpfiles:
            tmp_file = tmpfiles.register('/path/to/temp/file.md')
            # ... do work ...
            tmpfiles.mark_success()  # Cleanup happens automatically on exit

    If an exception occurs or mark_success() is not called, files are retained
    when debug_mode=True for troubleshooting.
    """

    def __init__(
        self,
        tmp_dir: Optional[Path] = None,
        debug_mode: bool = False,
        retention_days: int = 7
    ):
        """
        Initialize TempFileManager.

        Args:
            tmp_dir: Directory for temporary files (defaults to ./tmp_extractions)
            debug_mode: If True, retain files on failure for debugging
            retention_days: Days to retain files when cleaning up old temps
        """
        self.tmp_dir = tmp_dir or Path.cwd() / 'tmp_extractions'
        self.debug_mode = debug_mode
        self.retention_days = retention_days
        self.tracked_files: List[Path] = []
        self.success = False

    def __enter__(self):
        """Enter context manager."""
        # Ensure tmp directory exists
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"TempFileManager initialized with tmp_dir={self.tmp_dir}, debug_mode={self.debug_mode}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Exit context manager and cleanup files.

        Files are cleaned up if:
        - Operation succeeded (mark_success() was called), OR
        - Operation failed AND debug_mode is False
        """
        should_cleanup = self.success or (not self.debug_mode)

        if should_cleanup:
            self._cleanup_tracked_files()
        else:
            logger.info(f"Debug mode: Retaining {len(self.tracked_files)} temp files for inspection")
            for file_path in self.tracked_files:
                logger.info(f"  - {file_path}")

        # Don't suppress exceptions
        return False

    def register(self, file_path: str | Path) -> Path:
        """
        Register a temporary file for tracking.

        Args:
            file_path: Path to temporary file

        Returns:
            Path object for the registered file
        """
        path = Path(file_path)
        self.tracked_files.append(path)
        logger.debug(f"Registered temp file: {path}")
        return path

    def mark_success(self):
        """Mark operation as successful, enabling cleanup."""
        self.success = True
        logger.debug("Operation marked as successful")

    def _cleanup_tracked_files(self):
        """Remove all tracked temporary files."""
        removed_count = 0
        for file_path in self.tracked_files:
            try:
                if file_path.exists():
                    file_path.unlink()
                    removed_count += 1
                    logger.debug(f"Removed temp file: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to remove temp file {file_path}: {e}")

        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} temporary file(s)")

    @staticmethod
    def cleanup_old_files(tmp_dir: Path, days: int = 7) -> int:
        """
        Remove temporary files older than specified days.

        This is a standalone utility function for periodic cleanup tasks.

        Args:
            tmp_dir: Directory containing temporary files
            days: Remove files older than this many days

        Returns:
            Number of files removed
        """
        if not tmp_dir.exists():
            logger.info(f"Temp directory {tmp_dir} does not exist, nothing to clean")
            return 0

        cutoff_time = time.time() - (days * 86400)  # Convert days to seconds
        removed_count = 0

        try:
            for file_path in tmp_dir.iterdir():
                if not file_path.is_file():
                    continue

                # Check file age
                file_mtime = file_path.stat().st_mtime
                if file_mtime < cutoff_time:
                    try:
                        file_path.unlink()
                        removed_count += 1
                        age_days = (time.time() - file_mtime) / 86400
                        logger.debug(f"Removed old temp file: {file_path} (age: {age_days:.1f} days)")
                    except Exception as e:
                        logger.warning(f"Failed to remove old file {file_path}: {e}")

        except Exception as e:
            logger.error(f"Error during old file cleanup: {e}")

        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} old temporary file(s) from {tmp_dir}")

        return removed_count


def cleanup_old_tmp_files(
    tmp_dir: Optional[Path] = None,
    retention_days: int = 7
) -> int:
    """
    Convenience function to clean up old temporary files.

    Args:
        tmp_dir: Directory containing temporary files (defaults to ./tmp_extractions)
        retention_days: Remove files older than this many days

    Returns:
        Number of files removed
    """
    tmp_dir = tmp_dir or Path.cwd() / 'tmp_extractions'
    return TempFileManager.cleanup_old_files(tmp_dir, retention_days)


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Demo: Create test files and clean them up
    print("Demo: TempFileManager")
    print("=" * 50)

    # Test 1: Successful operation with cleanup
    print("\nTest 1: Successful operation (files should be cleaned up)")
    with TempFileManager(debug_mode=False) as tmpfiles:
        tmp_file1 = tmpfiles.register('tmp_extractions/test1.txt')
        tmp_file1.write_text('Test content 1')
        tmpfiles.mark_success()
    # Files should be removed

    # Test 2: Failed operation in debug mode (files retained)
    print("\nTest 2: Failed operation in debug mode (files should be retained)")
    try:
        with TempFileManager(debug_mode=True) as tmpfiles:
            tmp_file2 = tmpfiles.register('tmp_extractions/test2.txt')
            tmp_file2.write_text('Test content 2')
            raise ValueError("Simulated error")
    except ValueError:
        pass
    # File should be retained

    # Test 3: Cleanup old files
    print("\nTest 3: Cleanup old temporary files")
    # Create an old file by manually setting its modification time
    old_file = Path('tmp_extractions/old_test.txt')
    old_file.write_text('Old content')
    # Set modification time to 10 days ago
    old_time = time.time() - (10 * 86400)
    os.utime(old_file, (old_time, old_time))

    removed = cleanup_old_tmp_files(retention_days=7)
    print(f"Removed {removed} old file(s)")

    print("\nDemo complete!")
