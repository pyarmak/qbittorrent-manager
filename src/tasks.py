#!/usr/bin/env python3

import time
import core

# Import logging
try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-tasks')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-tasks')

# Import classes
from classes import TorrentInfo

# Import tag functions
from tags import auto_tag_torrent

# Import qBittorrent functions
from qbit import get_torrent_info

# Type checking imports
import typing
if typing.TYPE_CHECKING:
    from qbittorrentapi import Client as QBittorrentClient

# ===================================================================
# Unified Processing Function
# ===================================================================

def process_torrent_unified(client: 'QBittorrentClient', torrent_info: TorrentInfo) -> bool:
    """
    Unified torrent processing function.
    
    This is the single entry point for all torrent processing. It intelligently
    handles both complete and incomplete TorrentInfo objects, filling missing
    data as needed.
    
    Args:
        client: qBittorrent client instance
        torrent_info: TorrentInfo object (may have complete or minimal data)
        
    Returns:
        bool: Success status of processing
    """
    logger.info(f"--- Processing torrent (UNIFIED): {torrent_info.hash} ---")
    start_process_time = time.time()
    
    try:
        # Check if we have complete torrent information
        has_complete_data = (
            torrent_info.name and 
            torrent_info.content_path and 
            torrent_info.size > 0
        )
        
        if has_complete_data:
            logger.info(f"Complete torrent data available: {torrent_info.name}")
            logger.info(f"Files: {torrent_info.num_files} ({'multi-file' if torrent_info.is_multi_file else 'single-file'})")
            logger.info(f"Size: {torrent_info.size / (1024**3):.2f} GB")
            logger.info(f"Category: {torrent_info.category}")
            
            # Auto-tag torrent based on location if enabled
            if torrent_info.tags:
                logger.info(f"Current tags: {torrent_info.tags}")
            auto_tag_torrent(client, torrent_info, torrent_info.tags)
            
            # Use optimized processing (no additional API calls needed)
            success = core.process_single_torrent_optimized(client, torrent_info)
            
        else:
            logger.info(f"Incomplete torrent data for {torrent_info.hash}. Fetching missing information...")
            
            try:
                # Fill missing data via API
                complete_torrent_info = get_torrent_info(client, torrent_info.hash, wait_for_stability=True)
                if complete_torrent_info is None:
                    logger.error(f"Failed to fetch complete torrent info for {torrent_info.hash}")
                    return False
                
                logger.info(f"Complete torrent data fetched: {complete_torrent_info.name}")
                logger.info(f"Files: {complete_torrent_info.num_files} ({'multi-file' if complete_torrent_info.is_multi_file else 'single-file'})")
                logger.info(f"Size: {complete_torrent_info.size / (1024**3):.2f} GB")
                logger.info(f"Category: {complete_torrent_info.category}")
                
                # Auto-tag torrent based on location if enabled
                if complete_torrent_info.tags:
                    logger.info(f"Current tags: {complete_torrent_info.tags}")
                auto_tag_torrent(client, complete_torrent_info, complete_torrent_info.tags)
                
                # Use optimized processing with complete data
                success = core.process_single_torrent_optimized(client, complete_torrent_info)
                
            except Exception as e:
                logger.error(f"Failed to fetch complete torrent info: {e}")
                return False
        
        if success:
            logger.info(f"--- Successfully processed torrent: {torrent_info.hash} ---")
        else:
            logger.error(f"--- Failed to process torrent: {torrent_info.hash} ---")
        
        processing_time = time.time() - start_process_time
        logger.info(f"--- Finished processing in {processing_time:.2f} seconds ---")
        return success
            
    except Exception as e:
        logger.error(f"Error in unified torrent processing: {e}")
        return False
