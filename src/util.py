#!/usr/bin/env python3

import os
import shutil
import time
import signal
import functools
from contextlib import contextmanager

# Import classes from classes module
from classes import TimeoutError, LockError

# Import logging
try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-util')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-util')


# ===================================================================
# Timeout and Retry Utilities
# ===================================================================
@contextmanager
def timeout_context(seconds):
    """Context manager that raises TimeoutError if code takes too long"""
    def timeout_handler(signum, frame):
        raise TimeoutError(f"Operation timed out after {seconds} seconds")
    
    # Set up signal handler
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(seconds)
    
    try:
        yield
    finally:
        signal.alarm(0)  # Cancel alarm
        signal.signal(signal.SIGALRM, old_handler)  # Restore old handler

def retry_with_backoff(max_attempts=3, base_delay=1, max_delay=30):
    """Decorator for retrying functions with exponential backoff"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt == max_attempts:
                        logger.error(f"Function {func.__name__} failed after {max_attempts} attempts")
                        raise
                    
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(f"Attempt {attempt} failed for {func.__name__}: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
            
            raise last_exception
        return wrapper
    return decorator

# ===================================================================


# ===================================================================
# Helper Functions
# ===================================================================
def get_available_space_gb(path):
    """Gets available disk space in GB for the given path using shutil."""
    try:
        usage = shutil.disk_usage(path)
        available_gb = usage.free / (1024**3)
        return available_gb
    except FileNotFoundError:
        logger.error(f"Path '{path}' not found for disk usage check.")
        return None
    except Exception as e:
        logger.error(f"Error getting disk usage for {path}: {e}")
        return None

def get_dir_stats(path):
    """Calculates total size (bytes) and item count (files+dirs) for a directory path."""
    total_size = 0; item_count = 1
    if not os.path.isdir(path): return 0, 0
    try:
        for dirpath, dirnames, filenames in os.walk(path, topdown=True, onerror=lambda e: logger.warning(f"os.walk error: {e}")):
            item_count += len(dirnames) + len(filenames)
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    try: total_size += os.path.getsize(fp)
                    except OSError as e: logger.warning(f"Could not get size of {fp}: {e}")
    except OSError as e: logger.warning(f"Error walking directory {path}: {e}")
    return total_size, item_count

def cleanup_destination(path):
    """Attempts to remove a file or directory, used for cleaning up failed copies."""
    logger.info(f"Attempting to cleanup possibly incomplete destination: {path}")
    
    # Import config to check DRY_RUN flag
    import config
    
    if config.DRY_RUN:
        if os.path.isdir(path):
            logger.info(f"[DRY RUN] Would remove directory: {path}")
        elif os.path.isfile(path):
            logger.info(f"[DRY RUN] Would remove file: {path}")
        else:
            logger.info(f"[DRY RUN] Path not found, no cleanup needed: {path}")
        return
    
    try:
        if os.path.isdir(path): 
            shutil.rmtree(path)
            logger.info("Cleanup successful (removed directory).")
        elif os.path.isfile(path): 
            os.remove(path)
            logger.info("Cleanup successful (removed file).")
        else: 
            logger.info("Cleanup skipped (path not found).")
    except OSError as e: 
        logger.error(f"Cleanup FAILED: {e}")


def _iter_parents(path, stop_path):
    """Yields each directory from `path` up to (but excluding) `stop_path`."""
    if not path or not stop_path:
        return
    current = os.path.normpath(path)
    stop = os.path.normpath(stop_path)
    while current != stop and current != os.path.dirname(current):
        yield current
        current = os.path.dirname(current)


def ensure_destination_parent(dest_path, base_dir):
    """
    Prepares the parent directory tree for `dest_path` underneath `base_dir`.

    Torrents whose content lives inside a root folder need that folder to exist on
    the destination disk. Earlier releases copied such torrents to a *file* named
    after the torrent (e.g. `<base_dir>/Movie_Folder` holding Movie.mkv's bytes),
    which now blocks creation of the directory of the same name, so any stale
    non-directory entry along the way is removed.

    Returns True if the destination parent is ready to receive the copy.
    """
    import config

    parent = os.path.dirname(os.path.normpath(dest_path))
    if not parent:
        logger.error(f"Cannot determine parent directory for destination '{dest_path}'")
        return False

    # Work top-down so an outer stale file is removed before inner directories.
    for stale in reversed(list(_iter_parents(parent, base_dir))):
        if os.path.lexists(stale) and not os.path.isdir(stale):
            logger.warning(
                f"Destination path component '{stale}' exists but is not a directory "
                f"(leftover from an incorrectly named copy). Removing it."
            )
            if config.DRY_RUN:
                logger.info(f"[DRY RUN] Would remove stale entry: {stale}")
                continue
            try:
                os.remove(stale)
            except OSError as e:
                logger.error(f"Failed to remove stale destination entry '{stale}': {e}")
                return False

    if config.DRY_RUN:
        if not os.path.isdir(parent):
            logger.info(f"[DRY RUN] Would create destination directory: {parent}")
        return True

    try:
        os.makedirs(parent, exist_ok=True)
        return True
    except OSError as e:
        logger.error(f"Failed to create destination directory '{parent}': {e}")
        return False


def prune_empty_dirs(start_path, stop_path):
    """
    Removes empty directories from `start_path` upwards, stopping at `stop_path`.

    Used after deleting the content of a torrent that lived inside its own root
    folder so the now-empty folder does not linger on the cache disk. Only
    directories strictly inside `stop_path` are touched, and only while empty.
    """
    import config

    if not start_path or not stop_path:
        return

    start = os.path.normpath(start_path)
    stop = os.path.normpath(stop_path)

    # Never walk outside the boundary the caller asked us to stay within.
    try:
        if start != stop and os.path.commonpath([start, stop]) != stop:
            logger.debug(f"Skipping prune: '{start}' is not inside '{stop}'")
            return
    except ValueError:
        return

    for directory in _iter_parents(start, stop):
        if not os.path.isdir(directory) or os.path.islink(directory):
            break
        try:
            if os.listdir(directory):
                break
        except OSError as e:
            logger.warning(f"Could not inspect directory '{directory}': {e}")
            break

        if config.DRY_RUN:
            logger.info(f"[DRY RUN] Would remove empty directory: {directory}")
            break

        try:
            os.rmdir(directory)
            logger.info(f"Removed empty directory: {directory}")
        except OSError as e:
            logger.warning(f"Could not remove empty directory '{directory}': {e}")
            break


def verify_copy(src_path, dst_path, is_directory):
    """Verifies copy using size (single file) or size+count (directory).

    `is_directory` must describe whether `src_path` is a directory, which is not
    the same as the torrent being multi-file: a torrent with a single file inside
    a root folder has a file as its content path.
    """
    logger.debug("Verifying copy...")
    if not src_path or not dst_path:
        logger.error(f"Verification ERROR: Invalid paths provided - src: '{src_path}', dst: '{dst_path}'")
        return False
    if not os.path.exists(src_path): 
        logger.error(f"Verification ERROR: Source path '{src_path}' disappeared!")
        return False
    if not os.path.exists(dst_path): 
        logger.error(f"Verification ERROR: Destination path '{dst_path}' does not exist!")
        return False
    if os.path.isdir(src_path) != os.path.isdir(dst_path):
        logger.error(
            f"Verification FAILED! Source and destination types differ "
            f"(source is a {'directory' if os.path.isdir(src_path) else 'file'}, "
            f"destination is a {'directory' if os.path.isdir(dst_path) else 'file'})."
        )
        return False
    try:
        if not is_directory: # Single file comparison
            src_size = os.path.getsize(src_path)
            dst_size = os.path.getsize(dst_path)
            logger.debug(f"Source File Size: {src_size}")
            logger.debug(f"Dest File Size  : {dst_size}")
            if src_size == dst_size and src_size >= 0: 
                logger.info("Verification successful (file sizes match).")
                return True
            else: 
                logger.error("Verification FAILED! File sizes mismatch or invalid.")
                return False
        else: # Multi-file directory comparison
            src_size, src_count = get_dir_stats(src_path)
            dst_size, dst_count = get_dir_stats(dst_path)
            logger.debug(f"Source Dir : Size={src_size}, Items={src_count}")
            logger.debug(f"Dest Dir   : Size={dst_size}, Items={dst_count}")
            if src_size == dst_size and src_count == dst_count and src_count > 0 and src_size >= 0: 
                logger.info("Verification successful (total size/item count match).")
                return True
            elif src_size == 0 and dst_size == 0 and src_count == dst_count: 
                logger.info("Verification successful (both source and dest seem empty/zero size).")
                return True
            else: 
                logger.error("Verification FAILED! Size or item count mismatch.")
                return False
    except OSError as e: 
        logger.error(f"Verification ERROR: Could not get stats for paths '{src_path}' or '{dst_path}': {e}")
        return False

# ===================================================================

