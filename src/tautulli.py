#!/usr/bin/env python3
"""
Tautulli Integration for qBittorrent Manager

This module provides functions for checking Plex streaming status via Tautulli API
to ensure files are not deleted while being actively streamed.
"""

import os
import requests
from typing import List, Dict, Optional

# Import logging
try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-tautulli')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-tautulli')

# ===================================================================
# Tautulli API Functions
# ===================================================================

def get_currently_streaming_files(tautulli_url: str, tautulli_api_key: str) -> List[str]:
    """
    Get all currently streaming files from Tautulli API (single API call)
    
    Args:
        tautulli_url: Tautulli base URL
        tautulli_api_key: Tautulli API key
    
    Returns:
        List[str]: List of file paths currently being streamed (in Plex container paths)
    """
    if not tautulli_url or not tautulli_api_key:
        logger.warning("Tautulli URL or API key not configured - cannot check streaming status")
        return []
    
    try:
        # Get current activity from Tautulli
        response = requests.get(
            f"{tautulli_url.rstrip('/')}/api/v2",
            params={
                'apikey': tautulli_api_key,
                'cmd': 'get_activity'
            },
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        
        if data.get('response', {}).get('result') != 'success':
            logger.warning(f"Tautulli API returned error: {data.get('response', {}).get('message', 'Unknown error')}")
            return []
        
        sessions = data.get('response', {}).get('data', {}).get('sessions', [])
        streaming_files = []
        
        # Extract file paths from active sessions
        for session in sessions:
            session_file = session.get('file', '')
            session_state = session.get('state', '')
            
            # Only include actively playing/paused/buffering files
            if session_file and session_state in ['playing', 'paused', 'buffering']:
                streaming_files.append(session_file)
        
        logger.debug(f"Found {len(streaming_files)} currently streaming files")
        return streaming_files
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get streaming files from Tautulli: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error getting streaming files from Tautulli: {e}")
        return []

# ===================================================================
# Path Mapping Functions
# ===================================================================

def convert_local_path_to_plex_path(local_path: str) -> str:
    """
    Convert a local qbit-manager path to the equivalent Plex container path
    
    Args:
        local_path: Path as seen by qbit-manager
    
    Returns:
        str: Equivalent path in Plex container
    """
    import config
    
    abs_local_path = os.path.abspath(local_path)
    
    # Try each mapping from longest to shortest path (most specific first)
    sorted_mappings = sorted(config.PLEX_PATH_MAPPINGS.items(), key=lambda x: len(x[0]), reverse=True)
    
    for local_mount, plex_mount in sorted_mappings:
        local_mount_abs = os.path.abspath(local_mount)
        
        # Check if the local path starts with this mount point
        try:
            # Get relative path from mount point
            rel_path = os.path.relpath(abs_local_path, local_mount_abs)
            
            # If relpath doesn't start with '..', it's within this mount
            if not rel_path.startswith('..'):
                # Convert to Plex path
                if rel_path == '.':
                    plex_path = plex_mount
                else:
                    plex_path = os.path.join(plex_mount, rel_path)
                
                # Normalize path separators for consistency
                plex_path = plex_path.replace('\\', '/')
                logger.debug(f"Mapped local path {local_path} -> Plex path {plex_path}")
                return plex_path
                
        except ValueError:
            # os.path.relpath raises ValueError if paths are on different drives (Windows)
            continue
    
    # No mapping found, return original path
    logger.debug(f"No Plex path mapping found for {local_path}, using original path")
    return local_path

def paths_refer_to_same_file_with_mapping(local_path: str, plex_path: str) -> bool:
    """
    Check if a local path and Plex path refer to the same file using path mappings
    
    Args:
        local_path: Path as seen by qbit-manager
        plex_path: Path as seen by Plex/Tautulli
    
    Returns:
        bool: True if paths refer to the same file
    """
    # Convert local path to equivalent Plex path
    mapped_plex_path = convert_local_path_to_plex_path(local_path)
    
    # Normalize both paths for comparison
    mapped_plex_path_norm = os.path.normpath(mapped_plex_path).replace('\\', '/')
    plex_path_norm = os.path.normpath(plex_path).replace('\\', '/')
    
    # Direct path comparison
    if mapped_plex_path_norm == plex_path_norm:
        return True
    
    # For directory comparisons, check if one path is within the other
    try:
        # Check if plex_path is within mapped_plex_path
        rel_path1 = os.path.relpath(plex_path_norm, mapped_plex_path_norm)
        if not rel_path1.startswith('..'):
            return True
            
        # Check if mapped_plex_path is within plex_path  
        rel_path2 = os.path.relpath(mapped_plex_path_norm, plex_path_norm)
        if not rel_path2.startswith('..'):
            return True
            
    except ValueError:
        # Different drives or invalid paths
        pass
    
    return False

# ===================================================================
# High-level Streaming Check Functions
# ===================================================================

def is_file_currently_streaming(file_path: str, tautulli_url: str, tautulli_api_key: str) -> bool:
    """
    Check if a file is currently being streamed via Tautulli API
    
    Args:
        file_path: Path to file to check
        tautulli_url: Tautulli base URL
        tautulli_api_key: Tautulli API key
    
    Returns:
        bool: True if file is currently streaming, False otherwise
    """
    streaming_files = get_currently_streaming_files(tautulli_url, tautulli_api_key)
    
    for streaming_file in streaming_files:
        if paths_refer_to_same_file_with_mapping(file_path, streaming_file):
            logger.info(f"File is currently streaming: {file_path}")
            return True
    
    logger.debug(f"File is not currently streaming: {file_path}")
    return False

def get_streaming_status_for_directory(directory_path: str, tautulli_url: str, tautulli_api_key: str) -> Dict:
    """
    Get streaming status for all files in a directory (EFFICIENT - single API call)
    
    Args:
        directory_path: Directory to check
        tautulli_url: Tautulli base URL
        tautulli_api_key: Tautulli API key
    
    Returns:
        Dict: Status information including streaming files
    """
    result = {
        'directory': directory_path,
        'is_any_file_streaming': False,
        'streaming_files': [],
        'total_files_checked': 0
    }
    
    if not os.path.exists(directory_path):
        result['error'] = 'Directory does not exist'
        return result
    
    try:
        # Step 1: Get all currently streaming files from Tautulli (single API call)
        streaming_files_from_tautulli = get_currently_streaming_files(tautulli_url, tautulli_api_key)
        
        if not streaming_files_from_tautulli:
            # No files streaming at all, we can return immediately
            logger.debug(f"No files currently streaming according to Tautulli")
            return result
        
        # Step 2: Get all files in our target directory
        local_files = []
        if os.path.isfile(directory_path):
            local_files = [directory_path]
        else:
            for root, dirs, files in os.walk(directory_path):
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    local_files.append(file_path)
        
        result['total_files_checked'] = len(local_files)
        
        # Step 3: Check which local files match streaming files (efficient comparison)
        for local_file in local_files:
            for streaming_file in streaming_files_from_tautulli:
                if paths_refer_to_same_file_with_mapping(local_file, streaming_file):
                    result['streaming_files'].append(local_file)
                    result['is_any_file_streaming'] = True
                    logger.info(f"Found streaming file in directory: {local_file}")
                    break  # Found match, no need to check other streaming files
        
        if result['is_any_file_streaming']:
            logger.info(f"Directory {directory_path} has {len(result['streaming_files'])} streaming files")
        else:
            logger.debug(f"No streaming files found in directory {directory_path}")
        
        return result
        
    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Error checking streaming status for directory {directory_path}: {e}")
        return result
