#!/usr/bin/env python3

import os
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum

# ===================================================================
# Helper Classes
# ===================================================================

class BTIH(str):
    """
    Represents a BitTorrent Info Hash (BTIH) string.
    
    Enforces that the value is a string of exactly 32 or 40 characters
    containing only alphanumeric characters (a-z, A-Z, 0-9).
    
    Note: In qBittorrent external program parameters:
    - %I provides the info hash v1 (SHA-1, 40 characters)
    - %J provides the info hash v2 (SHA-256, 64 characters) 
    - We primarily use %I (v1) for compatibility with Arr applications
    """
    # Pre-compile regex patterns for efficiency
    _VALID_BTIH_32_PATTERN = re.compile(r'^[a-fA-F0-9]{32}$')  # Base32 encoded (DHT)
    _VALID_BTIH_40_PATTERN = re.compile(r'^[a-fA-F0-9]{40}$')  # SHA-1 hex (standard)
    _VALID_BTIH_64_PATTERN = re.compile(r'^[a-fA-F0-9]{64}$')  # SHA-256 hex (v2)

    def __new__(cls, value):
        # Basic type check
        if not isinstance(value, str):
            raise TypeError(f"Expected a string for BTIH, but got {type(value).__name__}")

        # Length and format validation
        length = len(value)
        if length == 32:
            if not cls._VALID_BTIH_32_PATTERN.fullmatch(value):
                raise ValueError("32-character BTIH must contain only hexadecimal characters (0-9, a-f, A-F)")
        elif length == 40:
            if not cls._VALID_BTIH_40_PATTERN.fullmatch(value):
                raise ValueError("40-character BTIH must contain only hexadecimal characters (0-9, a-f, A-F)")
        elif length == 64:
            if not cls._VALID_BTIH_64_PATTERN.fullmatch(value):
                raise ValueError("64-character BTIH must contain only hexadecimal characters (0-9, a-f, A-F)")
        else:
            raise ValueError(f"BTIH must be 32, 40, or 64 characters long, got {length}")

        # Create the string object using the parent's __new__
        instance = super().__new__(cls, value)
        return instance

    def __repr__(self):
        return f"BTIH('{super().__str__()}')"

# ===================================================================
# Service Status and Process Management
# ===================================================================

class ServiceStatus(Enum):
    """Service process status enumeration"""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class ProcessInfo:
    """Information about a running background process"""
    id: str
    torrent_hash: str
    start_time: float
    status: ServiceStatus
    result: Optional[Dict] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None

@dataclass
class QueueItem:
    """Item in the processing queue"""
    id: str
    torrent: 'TorrentInfo'  # Forward reference since TorrentInfo is defined later
    queued_time: float
    priority: int = 0  # Higher priority = processed first

# ===================================================================
# Path Helpers
# ===================================================================

def is_directory_content(content_path: str, num_files: int) -> bool:
    """
    Decide whether torrent content should be handled as a directory or a file.

    File count alone is not enough: qBittorrent reports a torrent whose single
    file lives inside a root folder as ``num_files == 1`` while ``content_path``
    still points at the file, so the filesystem is consulted whenever the content
    is present.

    Examples (content_path -> result):
        ``/dl/Movie.mkv`` (1 file, no folder)            -> False (a file)
        ``/dl/Movie_Folder/Movie.mkv`` (1 file in folder) -> False (a file)
        ``/dl/Show.S01`` (many files)                     -> True  (a directory)
    """
    if content_path and os.path.exists(content_path):
        return os.path.isdir(content_path)
    return num_files > 1


def _relative_to(path: str, base: str) -> str:
    """
    Return ``path`` expressed relative to ``base``, or "" when not contained.

    Only strict containment counts: a path equal to its base has no meaningful
    relative layout to reproduce elsewhere.
    """
    if not path or not base:
        return ""

    path = os.path.normpath(path)
    base = os.path.normpath(base)
    if path == base:
        return ""

    try:
        if os.path.commonpath([path, base]) != base:
            return ""
    except ValueError:
        # Mixed absolute/relative paths (or different drives on Windows)
        return ""

    return os.path.relpath(path, base)

# ===================================================================
# Enhanced TorrentInfo Class
# ===================================================================

@dataclass
class TorrentInfo:
    """
    Comprehensive torrent information class aligned with qBittorrent parameters.
    
    This class maps directly to qBittorrent's external program parameters:
    %N: Torrent name
    %L: Category  
    %G: Tags (separated by comma)
    %F: Content path (same as root path for multifile torrent)
    %R: Root path (first torrent subdirectory path)
    %D: Save path
    %C: Number of files
    %Z: Torrent size (bytes)
    %T: Current tracker
    %I: Info hash v1 (primary hash - 40 chars SHA-1)
    %J: Info hash v2 (64 chars SHA-256, optional)
    %K: Torrent ID (qBittorrent internal ID)
    """
    
    # Required fields (no defaults)
    hash_v1: BTIH                    # %I - Info hash v1 (SHA-1, primary)
    name: str                        # %N - Torrent name
    content_path: str                # %F - Content path (absolute path of torrent content)
    save_path: str                   # %D - Save path (where files are stored)
    size: int                        # %Z - Torrent size in bytes
    num_files: int                   # %C - Number of files
    
    # Optional fields (with defaults)
    root_path: str = ""              # %R - Root path (first common subfolder, empty if no root)
    category: str = ""               # %L - Category (replaces legacy 'label')
    tags: str = ""                   # %G - Tags (comma-separated)
    current_tracker: str = ""        # %T - Current tracker
    hash_v2: Optional[BTIH] = None   # %J - Info hash v2 (SHA-256, 64 chars)
    torrent_id: str = ""             # %K - qBittorrent internal torrent ID
    
    # Computed properties for backward compatibility
    @property
    def hash(self) -> BTIH:
        """Primary hash for backward compatibility (uses v1)"""
        return self.hash_v1
    
    @property
    def path(self) -> str:
        """Content path for backward compatibility"""
        return self.content_path
    
    @property
    def directory(self) -> str:
        """Directory containing the torrent content"""
        return os.path.dirname(self.content_path) if self.content_path else ""
    
    @property
    def is_multi_file(self) -> bool:
        """True if torrent contains multiple files"""
        return self.num_files > 1

    @property
    def is_directory_content(self) -> bool:
        """
        True when content_path refers to a directory rather than a single file.

        A torrent with a single file inside a root folder reports num_files == 1
        while content_path still points at the file itself, so file count alone is
        not enough to decide between a directory copy and a file copy. The
        filesystem is authoritative when the content is present.
        """
        return is_directory_content(self.content_path, self.num_files)

    @property
    def content_relative_path(self) -> str:
        """
        Location of the torrent content relative to its save path.

        qBittorrent resolves every file of a torrent as `<save path>/<relative
        path>`, so changing the save path (``torrents_set_location``) only works
        if a copy on another disk reproduces that exact relative layout.

        Examples (save path ``/downloads``):
            single file             -> ``Movie.mkv``
            folder with one file    -> ``Movie_Folder/Movie.mkv``
            folder with many files  -> ``Show.S01``
        """
        content = os.path.normpath(self.content_path) if self.content_path else ""
        if not content:
            return self.name.strip()

        # Preferred anchor: the save path qBittorrent reports for this torrent.
        relative = _relative_to(content, self.save_path)
        if relative:
            return relative

        # Fallback anchor: the torrent root folder, which always sits directly
        # inside the save path, so its name is the first path component.
        root = os.path.normpath(self.root_path) if self.root_path else ""
        if root:
            if content == root:
                return os.path.basename(root)
            relative = _relative_to(content, root)
            if relative:
                return os.path.join(os.path.basename(root), relative)

        # Last resort: assume the content sits directly in the save path.
        return self.name.strip() or os.path.basename(content)

    @property
    def content_root_name(self) -> str:
        """First path component of the content relative to the save path."""
        relative = self.content_relative_path
        return relative.split(os.sep)[0] if relative else ""

    @property
    def effective_save_path(self) -> str:
        """
        Directory the content layout is anchored at.

        Normally the save path qBittorrent reports; derived from the content path
        when the API response did not include one.
        """
        if self.save_path:
            return os.path.normpath(self.save_path)

        content = os.path.normpath(self.content_path) if self.content_path else ""
        if not content:
            return ""

        anchor = content
        for _ in self.content_relative_path.split(os.sep):
            anchor = os.path.dirname(anchor)
        return anchor

    def get_destination_path(self, base_dir: str) -> str:
        """
        Absolute path this torrent's content must occupy under ``base_dir``.

        ``base_dir`` is the save path a copy is meant to be seeded from, so the
        content is placed at the same relative location it has on the source
        disk.
        """
        return os.path.join(base_dir, self.content_relative_path)

    def get_destination_root(self, base_dir: str) -> str:
        """Top level entry created under ``base_dir`` for this torrent."""
        root_name = self.content_root_name
        return os.path.join(base_dir, root_name) if root_name else base_dir
    
    @classmethod
    def from_qbittorrent_api(cls, torrent_dict: Dict[str, Any], files_count: Optional[int] = None) -> 'TorrentInfo':
        """
        Create TorrentInfo from qBittorrent API response.
        
        Args:
            torrent_dict: Raw torrent dictionary from qBittorrent API
            files_count: Number of files (if known, avoids additional API call)
        """
        return cls(
            hash_v1=BTIH(torrent_dict.get('hash', '')),
            name=torrent_dict.get('name', ''),
            content_path=torrent_dict.get('content_path', ''),
            save_path=torrent_dict.get('save_path', ''),
            root_path=torrent_dict.get('root_path', ''),
            size=torrent_dict.get('size', 0),
            num_files=files_count if files_count is not None else torrent_dict.get('num_complete', 1),
            category=torrent_dict.get('category', ''),
            tags=','.join(torrent_dict.get('tags', [])) if isinstance(torrent_dict.get('tags'), list) else torrent_dict.get('tags', ''),
            current_tracker=torrent_dict.get('tracker', ''),
            hash_v2=BTIH(torrent_dict.get('hash_v2', '')) if torrent_dict.get('hash_v2') and torrent_dict.get('hash_v2') not in ['', '-', 'None', 'null'] and len(torrent_dict.get('hash_v2', '')) >= 32 else None,
            torrent_id=str(torrent_dict.get('id', ''))
        )
    
    @classmethod  
    def from_qbittorrent_params(cls, params: Dict[str, Any]) -> 'TorrentInfo':
        """
        Create TorrentInfo from qBittorrent external program parameters.
        
        This is the most efficient method as it uses the parameters directly
        provided by qBittorrent without requiring additional API calls.
        
        Args:
            params: Dictionary with qBittorrent parameters (%N, %L, %F, etc.)
        """
        return cls(
            hash_v1=BTIH(params.get('hash', params.get('hash_v1', ''))),
            name=params.get('name', ''),
            content_path=params.get('content_path', ''),
            save_path=params.get('save_path', ''),
            root_path=params.get('root_path', ''),
            size=int(params.get('size', 0)),
            num_files=int(params.get('num_files', 1)),
            category=params.get('category', ''),
            tags=params.get('tags', ''),
            current_tracker=params.get('tracker', ''),
            hash_v2=BTIH(params.get('hash_v2', '')) if params.get('hash_v2') and params.get('hash_v2') not in ['', '-', 'None', 'null'] and len(params.get('hash_v2', '')) >= 32 else None,
            torrent_id=params.get('torrent_id', '')
        )
    
    @classmethod
    def from_hash_only(cls, torrent_hash: str) -> 'TorrentInfo':
        """
        Create minimal TorrentInfo from hash only (for legacy compatibility).
        
        This is used when we only have a hash and need to create a TorrentInfo
        object. The other fields will be empty and may need to be filled later
        via API calls.
        
        Args:
            torrent_hash: Torrent hash string
        """
        return cls(
            hash_v1=BTIH(torrent_hash),
            name="",  # Will be filled by API call
            content_path="",  # Will be filled by API call
            save_path="",  # Will be filled by API call
            size=0,  # Will be filled by API call
            num_files=1  # Default assumption
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert TorrentInfo to dictionary for serialization.
        
        Returns:
            Dictionary representation suitable for JSON serialization
        """
        return {
            'hash_v1': str(self.hash_v1),
            'name': self.name,
            'content_path': self.content_path,
            'save_path': self.save_path,
            'root_path': self.root_path,
            'size': self.size,
            'num_files': self.num_files,
            'category': self.category,
            'tags': self.tags,
            'current_tracker': self.current_tracker,
            'hash_v2': str(self.hash_v2) if self.hash_v2 else None,
            'torrent_id': self.torrent_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TorrentInfo':
        """
        Create TorrentInfo from dictionary (for deserialization).
        
        Args:
            data: Dictionary with TorrentInfo data
            
        Returns:
            TorrentInfo instance
        """
        return cls(
            hash_v1=BTIH(data['hash_v1']),
            name=data.get('name', ''),
            content_path=data.get('content_path', ''),
            save_path=data.get('save_path', ''),
            root_path=data.get('root_path', ''),
            size=data.get('size', 0),
            num_files=data.get('num_files', 1),
            category=data.get('category', ''),
            tags=data.get('tags', ''),
            current_tracker=data.get('current_tracker', ''),
            hash_v2=BTIH(data['hash_v2']) if data.get('hash_v2') else None,
            torrent_id=data.get('torrent_id', '')
        )

# ===================================================================
# Exception Classes
# ===================================================================

class TimeoutError(Exception):
    """Custom timeout exception for operations that exceed time limits"""
    pass

class LockError(Exception):
    """Custom exception for file locking failures"""
    pass

# ===================================================================
# Path Explanation for qBittorrent Parameters
# ===================================================================
"""
qBittorrent Path Parameters Explanation:

1. save_path (%D) - The base path where all torrent files and subfolders are stored.
2. root_path (%R) - Absolute path of the first common subfolder; empty if no root folder.
3. content_path (%F) - Absolute path of torrent content:
   - For multifile torrents: same as root_path (if exists) or save_path
   - For single-file torrents: absolute file path

Examples with save path `/home/user/torrents`:

Torrent A (multifile with root folder):
  torrentA/
    subdir1/file1
    file2
  
  save_path:    /home/user/torrents
  root_path:    /home/user/torrents/torrentA  
  content_path: /home/user/torrents/torrentA

Torrent B (multifile, "strip root folder" mode):
  subdir1/file1
  file2
  
  save_path:    /home/user/torrents
  root_path:    <empty>
  content_path: /home/user/torrents

Torrent C (single file):
  file1
  
  save_path:    /home/user/torrents
  root_path:    <empty>
  content_path: /home/user/torrents/file1

Torrent D (root folder containing a single file):
  torrentD/
    file1

  save_path:    /home/user/torrents
  root_path:    /home/user/torrents/torrentD
  content_path: /home/user/torrents/torrentD/file1
  num_files:    1  <-- reported as a "single file" torrent!

For our application, content_path (%F) is typically the most useful as it points
directly to the torrent content regardless of structure.

IMPORTANT: the torrent name is NOT a reliable destination name. For Torrent D the
name is `torrentD` while the content is a file one level deeper, so a copy has to
be placed at `<destination>/torrentD/file1`. `TorrentInfo.content_relative_path`
derives that layout (content_path relative to save_path) and must be used whenever
a copy is created for qBittorrent to seed from.
""" 