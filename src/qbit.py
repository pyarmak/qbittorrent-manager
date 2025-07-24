#!/usr/bin/env python3

import os
import time
import typing

# Import classes
from classes import BTIH, TorrentInfo, TimeoutError

# Import utilities
from util import retry_with_backoff, timeout_context

# Import logging
try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-qbit')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-qbit')

if typing.TYPE_CHECKING:
    from qbittorrentapi import TorrentDictionary
    from qbittorrentapi import Client as QBittorrentClient

# ===================================================================
# qBittorrent Client Singleton
# ===================================================================

# Global client instance for singleton pattern
_qbit_client_instance = None
_client_lock = None

def get_qbit_client():
    """
    Get a singleton qBittorrent client instance.
    
    This function implements a singleton pattern to reuse the same client connection,
    which is more efficient and reduces session overhead in qBittorrent.
    
    Returns:
        QBittorrentClient: Authenticated qBittorrent client instance
        
    Raises:
        ConnectionError: If unable to connect or authenticate
    """
    global _qbit_client_instance, _client_lock
    import config
    import qbittorrentapi
    
    # Initialize lock if needed
    if _client_lock is None:
        import threading
        _client_lock = threading.Lock()
    
    with _client_lock:
        # Return existing instance if available and healthy
        if _qbit_client_instance is not None:
            try:
                # Test connection by making a lightweight API call
                _qbit_client_instance.app.version
                logger.debug("Reusing existing qBittorrent client connection")
                return _qbit_client_instance
            except Exception as e:
                logger.warning(f"Existing qBittorrent client connection failed: {e}")
                logger.info("Creating new qBittorrent client connection...")
                _qbit_client_instance = None
        
        # Create new client instance
        logger.debug("Creating new qBittorrent client connection...")
        
        # Get connection info with performance optimizations
        connection_info = config.get_qbit_connection_info()
        connection_info.update({
            'SIMPLE_RESPONSES': False,  # Keep rich objects for better functionality
            'DISABLE_LOGGING_DEBUG_OUTPUT': True,  # Reduce log noise
            'REQUESTS_ARGS': {
                'timeout': 30,  # 30 second timeout
                'allow_redirects': True
            }
        })
        
        client = qbittorrentapi.Client(**connection_info)
        
        # Test connection with better error handling
        try:
            client.auth_log_in()
            app_version = client.app.version
            api_version = client.app.web_api_version
            logger.info(f"Connected to qBittorrent v{app_version} (Web API v{api_version})")
            
            # Store the working client
            _qbit_client_instance = client
            return _qbit_client_instance
            
        except qbittorrentapi.LoginFailed as e:
            raise ConnectionError(f"qBittorrent authentication failed: {e}")
        except qbittorrentapi.APIConnectionError as e:
            raise ConnectionError(f"Failed to connect to qBittorrent: {e}")
        except Exception as e:
            raise ConnectionError(f"Unexpected qBittorrent connection error: {e}")

def close_qbit_client():
    """
    Close the singleton qBittorrent client connection.
    
    This should be called when the application is shutting down to properly
    clean up the session in qBittorrent.
    """
    global _qbit_client_instance, _client_lock
    
    if _client_lock is None:
        return
        
    with _client_lock:
        if _qbit_client_instance is not None:
            try:
                _qbit_client_instance.auth_log_out()
                logger.debug("Logged out from qBittorrent client")
            except Exception as e:
                logger.warning(f"Error logging out from qBittorrent: {e}")
            finally:
                _qbit_client_instance = None

# ===================================================================
# qBittorrent API Functions
# ===================================================================

@retry_with_backoff(max_attempts=3, base_delay=2)
def get_torrent_info(client: 'QBittorrentClient', hash_val: BTIH, wait_for_stability=True) -> TorrentInfo:
    """
    Gets and parses torrent information using QBittorrentClient with timeout and retry logic

    param client: The QBittorrentClient instance to use for fetching torrent data.
    param hash_val: The BTIH hash value of the torrent to fetch info for.
    param wait_for_stability: If True, adds delay to wait for torrent state to stabilize
    return: A TorrentInfo object containing the torrent's name, path, directory, size, is_multi_file status, and label.
    example: TorrentInfo(hash=BTIH('02E5A8D9F7800A063237F0D37467144360D4B70A'), name='daredevil.born.again.s01e08.hdr.2160p.web.h265-successfulcrab.mkv', path='\\downloading\\sonarr\\daredevil.born.again.s01e08.hdr.2160p.web.h265-successfulcrab.mkv', directory='/downloading/sonarr', size=5408683456, is_multi_file=False, label='sonarr')
    """
    logger.debug("Getting torrent info...")
    
    # Add initial delay to let torrent state stabilize if requested
    if wait_for_stability:
        stability_delay = 3  # seconds
        logger.info(f"Waiting {stability_delay}s for torrent state to stabilize...")
        time.sleep(stability_delay)
    
    try:
        with timeout_context(30):  # 30 second timeout for getting torrent info
            torrents = client.torrents_info(torrent_hashes=str(hash_val))
            
            if not torrents:
                logger.error(f"Torrent {hash_val} not found.")
                return None
            
            # Get the first (and should be only) torrent
            torrent = torrents[0]
            
            # Determine if torrent is multi-file using proper qBittorrent API
            try:
                files_list = client.torrents_files(torrent_hash=torrent.hash)
                files_count = len(files_list)
            except:
                # Fallback - assume single file if API call fails
                files_count = 1
            
            # Convert torrent object to dictionary for the factory method
            torrent_dict = {
                'hash': torrent.hash,
                'name': torrent.name,
                'content_path': torrent.content_path,
                'save_path': getattr(torrent, 'save_path', ''),
                'size': torrent.size,
                'category': torrent.category or '',
                'tags': getattr(torrent, 'tags', []),
                'tracker': getattr(torrent, 'tracker', ''),
            }
            
            # Create TorrentInfo using the factory method
            torrent_info = TorrentInfo.from_qbittorrent_api(torrent_dict, files_count)
            logger.debug(f"Successfully retrieved info for torrent: {torrent_info.name}")
            return torrent_info
            
    except TimeoutError as e:
        logger.error(f"Timeout getting torrent info for {hash_val}: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to get torrent info for {hash_val}: {e}")
        raise

def get_torrents_by_path(client: 'QBittorrentClient', path: str, complete=True) -> typing.List['TorrentDictionary']:
    """
    Gets a list of torrent hashes that match the given path.

    param client: The QBittorrentClient instance to use for fetching torrent data.
    param path: The path to search for torrents.
    param complete: If True, only include torrents that are complete.
    return: A list of TorrentDictionary for torrents that match the given criteria.
    """
    logger.debug(f"Getting torrents by path: {path}")
    # QBittorrent doesn't have a direct "realpath" field in torrent_info,
    # so we'll search by path and then filter by completion status.
    # This is a simplified approach and might not be perfectly accurate
    # if multiple torrents have the same path but different completion status.
    
    # Fetch all torrents
    all_torrents = get_all_torrents(client)
    
    # Filter by path
    matching_torrents = [
        t for t in all_torrents if t.content_path == path
    ]
    
    # Optionally filter by completion status
    if complete:
        # In qBittorrent, check if torrent is complete by checking progress
        matching_torrents = [
            t for t in matching_torrents if t.progress == 1.0  # 1.0 = 100% complete
        ]
    
    logger.debug(f"Found {len(matching_torrents)} matches")
    return matching_torrents

def get_all_torrents(client: 'QBittorrentClient') -> typing.List['TorrentDictionary']:
    """
    Get all torrents from qBittorrent.
    
    Args:
        client: qBittorrent client instance
        
    Returns:
        List of TorrentDictionary objects for all torrents
    """
    logger.debug("Getting all torrents")
    try:
        return client.torrents_info(SIMPLE_RESPONSES=True)
    except Exception as e:
        logger.error(f"Failed to get all torrents: {e}")
        raise

def get_torrents_by_tag(client: 'QBittorrentClient', tag: str) -> typing.List['TorrentDictionary']:
    """
    Get torrents filtered by a specific tag.
    
    Args:
        client: qBittorrent client instance
        tag: Tag to filter by
        
    Returns:
        List of TorrentDictionary objects matching the tag
    """
    logger.debug(f"Getting torrents with tag: {tag}")
    try:
        return client.torrents_info(tag=tag)
    except Exception as e:
        logger.error(f"Failed to get torrents by tag '{tag}': {e}")
        raise

def get_torrents_by_status(client: 'QBittorrentClient', status: str) -> typing.List['TorrentDictionary']:
    """
    Get torrents filtered by status.
    
    Args:
        client: qBittorrent client instance
        status: Status filter (e.g., 'completed', 'downloading', etc.)
        
    Returns:
        List of TorrentDictionary objects matching the status
    """
    logger.debug(f"Getting torrents with status: {status}")
    try:
        return client.torrents_info(status_filter=status)
    except Exception as e:
        logger.error(f"Failed to get torrents by status '{status}': {e}")
        raise

def get_torrents_by_status_and_tag(client: 'QBittorrentClient', status: str, tag: str) -> typing.List['TorrentDictionary']:
    """
    Get torrents filtered by both status and tag.
    
    Args:
        client: qBittorrent client instance
        status: Status filter (e.g., 'completed', 'downloading', etc.)
        tag: Tag to filter by
        
    Returns:
        List of TorrentDictionary objects matching both status and tag
    """
    logger.debug(f"Getting torrents with status '{status}' and tag '{tag}'")
    try:
        return client.torrents_info(status_filter=status, tag=tag)
    except Exception as e:
        logger.error(f"Failed to get torrents by status '{status}' and tag '{tag}': {e}")
        raise

def get_torrent_by_hash(client: 'QBittorrentClient', hash_val: str) -> 'TorrentDictionary':
    """
    Get a specific torrent by its hash (lightweight version that returns raw torrent object).
    
    Args:
        client: qBittorrent client instance
        hash_val: Hash of the torrent to retrieve
        
    Returns:
        TorrentDictionary object for the specified torrent
        
    Raises:
        ValueError: If torrent not found
    """
    logger.debug(f"Getting torrent by hash: {hash_val}")
    try:
        torrents = client.torrents_info(torrent_hashes=str(hash_val))
        if not torrents:
            raise ValueError(f"Torrent {hash_val} not found")
        return torrents[0]
    except Exception as e:
        logger.error(f"Failed to get torrent by hash '{hash_val}': {e}")
        raise 