#!/usr/bin/env python3
"""
Sonarr/Radarr API utilities for qBittorrent Manager.

Provides functions to verify that a torrent has been successfully imported
by Sonarr or Radarr from the HDD location before SSD data is deleted.
"""

import os
import requests
from typing import Optional

try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-arr')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-arr')


# Sonarr/Radarr v3 history eventType integer for "downloadFolderImported".
# Confirmed via live API responses (the response body shows the string
# "downloadFolderImported" but the query parameter uses the integer 3).
_EVENT_TYPE_DOWNLOAD_FOLDER_IMPORTED = 3


# ===================================================================
# Import Verification
# ===================================================================

def verify_arr_import(
    torrent_hash: str,
    torrent_category: str,
    expected_hdd_path: str,
    sonarr_url: str,
    sonarr_api_key: str,
    radarr_url: str,
    radarr_api_key: str,
    sonarr_tag: str = 'sonarr',
    radarr_tag: str = 'radarr',
    timeout: int = 15,
) -> tuple[bool, str, list[str]]:
    """
    Verify that a torrent has been imported by Sonarr or Radarr and check
    whether the imported files are symlinks (need replacement) or hardlinks
    to HDD (already done).

    Workflow context
    ----------------
    When a torrent completes on SSD:
    1. qbit-manager copies it to HDD in the background
    2. qBittorrent save path still points to SSD
    3. Import script creates symlinks: arr library → SSD
    4. During space management, we need to:
       a. Verify HDD copy exists
       b. Check if arr has imported (has history records)
       c. Check if imported files are symlinks (need replacement) or hardlinks (done)
       d. Replace symlinks with hardlinks to HDD
       e. Update qBittorrent save path to HDD
       f. Delete SSD copy

    The importedPath field tells us where arr placed the file in its library.
    We check if this path is a symlink (pointing to SSD, needs replacement)
    or a hardlink to HDD (already done, safe to delete SSD).

    Example API response (Radarr):
      record["data"]["droppedPath"]  = "/downloads/flood/radarr/<torrent-name>/<file>"
      record["data"]["importedPath"] = "/downloads/radarr/Wreck-It Ralph (2012)/<file>"

    Example API response (Sonarr):
      record["data"]["droppedPath"]  = "/downloading/<torrent-name>.mkv"
      record["data"]["importedPath"] = "/downloads/sonarr/Taskmaster (AU)/Season 5/<file>"

    Args:
        torrent_hash:      Torrent info hash. Uppercased before sending —
                           Sonarr/Radarr require uppercase hashes as downloadId.
        torrent_category:  qBittorrent category (e.g. 'sonarr', 'radarr').
        expected_hdd_path: Full HDD path for this torrent's content
                           (e.g. /downloads/hdd/radarr/<torrent-name>).
                           Used to verify HDD copy exists.
        sonarr_url:        Base URL for Sonarr (e.g. 'http://sonarr:8989').
        sonarr_api_key:    Sonarr API key.
        radarr_url:        Base URL for Radarr (e.g. 'http://radarr:7878').
        radarr_api_key:    Radarr API key.
        sonarr_tag:        qBittorrent category name that maps to Sonarr.
        radarr_tag:        qBittorrent category name that maps to Radarr.
        timeout:           HTTP request timeout in seconds.

    Returns:
        tuple[bool, str, list[str]]:
            (True,  "imported_with_hardlinks", [paths]) — arr imported with hardlinks to HDD, safe to delete SSD
            (True,  "imported_with_symlinks", [paths])  — arr imported with symlinks to SSD, need replacement
            (False, "not_imported", [])                 — no downloadFolderImported record found
            (False, "api_error", [])                    — could not reach arr API
            (False, "not_configured", [])               — arr URL/key not set for this category
            (False, "unknown_category", [])             — category doesn't match sonarr or radarr
            
            The list contains all importedPath values from the history records.
    """
    category_lower = torrent_category.lower()

    if category_lower == sonarr_tag.lower():
        service = 'Sonarr'
        base_url = sonarr_url.rstrip('/') if sonarr_url else ''
        api_key = sonarr_api_key
    elif category_lower == radarr_tag.lower():
        service = 'Radarr'
        base_url = radarr_url.rstrip('/') if radarr_url else ''
        api_key = radarr_api_key
    else:
        logger.warning(
            f"Torrent category '{torrent_category}' does not match sonarr tag "
            f"('{sonarr_tag}') or radarr tag ('{radarr_tag}') — cannot verify import"
        )
        return False, "unknown_category", []

    if not base_url or not api_key:
        logger.warning(
            f"{service} URL or API key not configured — skipping import verification"
        )
        return False, "not_configured", []

    logger.debug(f"Verifying {service} import for torrent {torrent_hash}")

    records = _fetch_history(base_url, api_key, torrent_hash, service, timeout)
    if records is None:
        return False, "api_error", []

    if not records:
        logger.warning(
            f"{service} has no 'downloadFolderImported' history for torrent {torrent_hash}"
        )
        return False, "not_imported", []

    # Normalize the expected HDD path once for all comparisons
    hdd_path_norm = os.path.normpath(expected_hdd_path)

    # Collect all imported paths and check their link status
    imported_paths = []
    has_symlinks = False
    has_hardlinks = False

    for record in records:
        imported_path = _extract_imported_path(record)
        
        if not imported_path:
            logger.debug(f"{service} history record has no importedPath, skipping")
            continue

        imported_paths.append(imported_path)
        
        # Check if the imported path is a symlink or hardlink
        if os.path.islink(imported_path):
            has_symlinks = True
            symlink_target = os.readlink(imported_path)
            logger.debug(f"{service} imported file is a symlink: {imported_path} -> {symlink_target}")
        elif os.path.exists(imported_path):
            # Get file stats for detailed analysis
            try:
                file_stat = os.stat(imported_path)
                link_count = file_stat.st_nlink
                
                logger.debug(f"{service} imported file link count: {link_count} for {imported_path}")
                
                if link_count > 1:
                    # It's a hardlink - check if it's to HDD
                    # Note: Hardlinks to SSD are impossible since SSD and HDD are different filesystems
                    # So if link_count > 1, it must be hardlinked to something on the same filesystem as arr's library
                    if _is_hardlink_to_hdd(imported_path, hdd_path_norm):
                        has_hardlinks = True
                        logger.debug(f"{service} imported file is a hardlink to HDD: {imported_path}")
                    else:
                        # Hardlink to something else on the same filesystem (not our HDD copy)
                        # This could be a hardlink created by arr to another location in /downloads
                        # We need to replace it with a hardlink to our HDD seeding location
                        has_symlinks = True
                        logger.debug(f"{service} imported file is a hardlink but NOT to our HDD seeding location: {imported_path}")
                else:
                    # Regular file (link count = 1) - this is a copy, not a link
                    # Arr copied the file instead of linking it
                    has_symlinks = True
                    logger.debug(f"{service} imported file is a regular file copy (not linked): {imported_path}")
            except (OSError, ValueError) as e:
                logger.warning(f"{service} error checking file stats for {imported_path}: {e}")
                # Treat as needs replacement to be safe
                has_symlinks = True
        else:
            logger.warning(f"{service} imported path does not exist: {imported_path}")

    if not imported_paths:
        logger.warning(f"{service} has history records but no valid importedPath values")
        return False, "not_imported", []

    # Determine the result based on what we found
    if has_symlinks and not has_hardlinks:
        logger.info(
            f"✅ {service} has imported {len(imported_paths)} file(s) with symlinks "
            f"for {torrent_hash} — need to replace with hardlinks"
        )
        return True, "imported_with_symlinks", imported_paths
    elif has_hardlinks and not has_symlinks:
        logger.info(
            f"✅ {service} has imported {len(imported_paths)} file(s) with hardlinks to HDD "
            f"for {torrent_hash} — safe to delete SSD"
        )
        return True, "imported_with_hardlinks", imported_paths
    elif has_hardlinks and has_symlinks:
        # Mixed state - some files are hardlinks, some are symlinks
        # Treat as symlinks case (need replacement)
        logger.warning(
            f"{service} has mixed import state for {torrent_hash} "
            f"(some symlinks, some hardlinks) — treating as needs replacement"
        )
        return True, "imported_with_symlinks", imported_paths
    else:
        # No symlinks or hardlinks detected (shouldn't happen if files exist)
        logger.warning(
            f"{service} has imported files but link status unclear for {torrent_hash}"
        )
        return False, "not_imported", imported_paths


def _fetch_history(
    base_url: str,
    api_key: str,
    torrent_hash: str,
    service_name: str,
    timeout: int,
) -> Optional[list]:
    """
    Query the arr history API for downloadFolderImported events matching the
    given torrent hash.

    Returns a list of matching history records, an empty list if none found,
    or None on API error.

    Endpoint: GET /api/v3/history?downloadId=<HASH>&eventType=3&pageSize=50

    Notes:
    - downloadId must be uppercase — lowercase hashes return no results.
    - eventType=3 corresponds to "downloadFolderImported" in both Sonarr and
      Radarr v3 (the response body uses the string name, but the query param
      uses the integer).
    - The response is always a paged object: {"page":1, "records": [...], ...}
    """
    endpoint = f"{base_url}/api/v3/history"
    headers = {"X-Api-Key": api_key}
    params = {
        # Sonarr/Radarr require uppercase hashes — lowercase returns no results
        "downloadId": torrent_hash.upper(),
        "eventType": _EVENT_TYPE_DOWNLOAD_FOLDER_IMPORTED,
        "pageSize": 50,
    }

    try:
        response = requests.get(
            endpoint, headers=headers, params=params, timeout=timeout
        )
        response.raise_for_status()
        data = response.json()

        # v3 history always returns a paged envelope: {"records": [...], ...}
        # Guard against unexpected response shapes just in case.
        if isinstance(data, dict):
            records = data.get("records", [])
        elif isinstance(data, list):
            records = data
        else:
            logger.error(
                f"{service_name} history API returned unexpected type: {type(data)}"
            )
            return None

        logger.debug(
            f"{service_name} returned {len(records)} downloadFolderImported "
            f"record(s) for {torrent_hash.upper()}"
        )
        return records

    except requests.exceptions.ConnectionError:
        logger.error(
            f"Cannot connect to {service_name} at {base_url} — is it running?"
        )
        return None
    except requests.exceptions.Timeout:
        logger.error(f"{service_name} API timed out after {timeout}s")
        return None
    except requests.exceptions.HTTPError as e:
        logger.error(f"{service_name} API returned HTTP error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error querying {service_name} history: {e}")
        return None


def _extract_dropped_path(record: dict) -> Optional[str]:
    """
    Extract the source path from a downloadFolderImported history record.

    This is where arr picked the file up from — the download client / HDD path.
    Both Sonarr and Radarr store this in record["data"]["droppedPath"].

    Example: "/downloads/hdd/radarr/Wreck-It.Ralph.2012.../Wreck-It.Ralph....mkv"
    """
    try:
        return record.get("data", {}).get("droppedPath")
    except (AttributeError, TypeError):
        return None


def _extract_imported_path(record: dict) -> Optional[str]:
    """
    Extract the destination (library) path from a downloadFolderImported record.

    This is where arr placed the file in its library after import.
    Both Sonarr and Radarr store this in record["data"]["importedPath"].

    Example: "/downloads/radarr/Wreck-It Ralph (2012)/Wreck-It.Ralph....mkv"
    Used for informational logging only — we do not verify against this path.
    """
    try:
        return record.get("data", {}).get("importedPath")
    except (AttributeError, TypeError):
        return None


def _is_path_within_directory(file_path: str, directory_path: str) -> bool:
    """
    Return True if file_path is equal to or nested inside directory_path.
    Uses os.path.relpath to handle platform differences correctly.
    """
    try:
        if file_path == directory_path:
            return True
        rel = os.path.relpath(file_path, directory_path)
        return not rel.startswith('..')
    except ValueError:
        # Different drives on Windows
        return False


def _is_hardlink_to_hdd(imported_path: str, hdd_path_norm: str) -> bool:
    """
    Check if an imported file is a hardlink to a file within the HDD path.
    
    Args:
        imported_path: Path to the imported file in arr's library
        hdd_path_norm: Normalized HDD base path for this torrent
    
    Returns:
        bool: True if the file is a hardlink to something in the HDD path
    """
    try:
        if not os.path.exists(imported_path):
            return False
        
        # Get file stats
        imported_stat = os.stat(imported_path)
        
        # If hardlink count is 1, it's not a hardlink
        if imported_stat.st_nlink <= 1:
            return False
        
        # Walk through HDD directory to find files with same inode
        # This confirms the file is hardlinked to the HDD copy
        for root, dirs, files in os.walk(hdd_path_norm):
            for filename in files:
                hdd_file_path = os.path.join(root, filename)
                try:
                    hdd_stat = os.stat(hdd_file_path)
                    
                    # Check if same device and inode (indicates hardlink)
                    if (imported_stat.st_dev == hdd_stat.st_dev and 
                        imported_stat.st_ino == hdd_stat.st_ino and
                        imported_path != hdd_file_path):  # Different paths but same inode
                        logger.debug(f"Found hardlink: {imported_path} <-> {hdd_file_path}")
                        return True
                except (OSError, ValueError):
                    continue
        
        return False
        
    except Exception as e:
        logger.debug(f"Error checking hardlink for {imported_path}: {e}")
        return False
