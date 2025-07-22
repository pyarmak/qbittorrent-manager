#!/usr/bin/env python3

import os
import shutil
import time
import typing

# Import qBittorrent functions
from qbit import get_all_torrents, get_torrents_by_tag

# Import logging
try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-tags')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-tags')

if typing.TYPE_CHECKING:
    from qbittorrentapi import Client as QBittorrentClient

# ===================================================================
# Tag Management Functions
# ===================================================================

def tag_existing_torrents_by_location(client: 'QBittorrentClient', dry_run=False):
    """
    Tag existing torrents based on their current storage location.
    This is useful for initial setup or migrating to the tagging system.
    
    Enhanced to ensure all SSD torrents also have HDD copies:
    - If torrent is on SSD: Add SSD tag, check for HDD copy, copy if missing, add HDD tag
    - If torrent is on HDD: Add HDD tag
    
    Args:
        client: qBittorrent client instance
        dry_run: If True, only show what would be tagged without making changes
    
    Returns:
        dict: Summary of tagging operations including any copy operations
    """
    import config
    from util import verify_copy
    
    if not config.ENABLE_LOCATION_TAGGING:
        logger.warning("Location tagging is disabled in configuration")
        return {'error': 'Location tagging disabled'}
    
    logger.info("Analyzing torrents for location-based tagging...")
    
    try:
        # Get all torrents
        all_torrents = get_all_torrents(client)
        
        ssd_candidates = []
        hdd_candidates = []
        copy_operations = []
        untaggable = []
        
        for torrent in all_torrents:
            try:
                if not torrent.content_path:
                    untaggable.append({
                        'hash': torrent.hash,
                        'name': torrent.name,
                        'reason': 'No content path available'
                    })
                    continue
                
                # Check current tags
                current_tags = getattr(torrent, 'tags', '') or ''
                has_ssd_tag = config.SSD_LOCATION_TAG in current_tags
                has_hdd_tag = config.HDD_LOCATION_TAG in current_tags
                
                # Enhanced location analysis with dual-location support
                if torrent.content_path.startswith(config.DOWNLOAD_PATH_SSD):
                    # Torrent is currently pointing to SSD location
                    logger.debug(f"Analyzing SSD torrent: {torrent.name}")
                    
                    # Always ensure SSD tag is present
                    if not has_ssd_tag:
                        ssd_candidates.append({
                            'hash': torrent.hash,
                            'name': torrent.name,
                            'path': torrent.content_path,
                            'current_tags': current_tags,
                            'action': 'add_ssd_tag'
                        })
                    
                    # Check if HDD copy exists (determine expected HDD path)
                    try:
                        # Extract category from torrent
                        torrent_category = torrent.category or ''
                        if torrent_category:
                            expected_hdd_base = os.path.join(config.FINAL_DEST_BASE_HDD, torrent_category)
                            expected_hdd_path = os.path.join(expected_hdd_base, torrent.name.strip())
                            
                            if os.path.exists(expected_hdd_path):
                                # HDD copy exists - ensure HDD tag is present
                                logger.debug(f"HDD copy found for {torrent.name}")
                                if not has_hdd_tag:
                                    hdd_candidates.append({
                                        'hash': torrent.hash,
                                        'name': torrent.name,
                                        'path': torrent.content_path,
                                        'hdd_path': expected_hdd_path,
                                        'current_tags': current_tags,
                                        'action': 'add_hdd_tag'
                                    })
                            else:
                                # HDD copy missing - needs copy operation
                                logger.debug(f"HDD copy MISSING for {torrent.name}")
                                copy_operations.append({
                                    'hash': torrent.hash,
                                    'name': torrent.name,
                                    'ssd_path': torrent.content_path,
                                    'hdd_path': expected_hdd_path,
                                    'category': torrent_category,
                                    'current_tags': current_tags,
                                    'action': 'copy_and_tag_hdd'
                                })
                        else:
                            logger.warning(f"Torrent {torrent.name} has no category - cannot determine HDD path")
                            untaggable.append({
                                'hash': torrent.hash,
                                'name': torrent.name,
                                'path': torrent.content_path,
                                'reason': 'No category - cannot determine HDD path'
                            })
                    except Exception as e:
                        logger.warning(f"Error checking HDD path for {torrent.name}: {e}")
                        untaggable.append({
                            'hash': torrent.hash,
                            'name': torrent.name,
                            'path': torrent.content_path,
                            'reason': f'Error checking HDD path: {e}'
                        })
                        
                elif torrent.content_path.startswith(config.FINAL_DEST_BASE_HDD):
                    # Torrent is currently pointing to HDD location
                    logger.debug(f"Analyzing HDD torrent: {torrent.name}")
                    if not has_hdd_tag:
                        hdd_candidates.append({
                            'hash': torrent.hash,
                            'name': torrent.name,
                            'path': torrent.content_path,
                            'current_tags': current_tags,
                            'action': 'add_hdd_tag'
                        })
                else:
                    untaggable.append({
                        'hash': torrent.hash,
                        'name': torrent.name,
                        'path': torrent.content_path,
                        'reason': 'Path not in SSD or HDD location'
                    })
                    
            except Exception as e:
                logger.warning(f"Error analyzing torrent {getattr(torrent, 'hash', 'UNKNOWN')}: {e}")
                untaggable.append({
                    'hash': getattr(torrent, 'hash', 'UNKNOWN'),
                    'name': getattr(torrent, 'name', 'UNKNOWN'),
                    'reason': f'Analysis error: {e}'
                })
        
        # Summary
        total_operations = len(ssd_candidates) + len(hdd_candidates) + len(copy_operations)
        
        logger.info(f"Enhanced tagging analysis complete:")
        logger.info(f"  SSD tag operations: {len(ssd_candidates)}")
        logger.info(f"  HDD tag operations: {len(hdd_candidates)}")
        logger.info(f"  Copy + tag operations: {len(copy_operations)}")
        logger.info(f"  Untaggable torrents: {len(untaggable)}")
        
        if dry_run:
            logger.info("[DRY RUN] Would perform the following operations:")
            
            # SSD tagging operations
            for item in ssd_candidates:
                if item['action'] == 'add_ssd_tag':
                    logger.info(f"  🏷️  Add '{config.SSD_LOCATION_TAG}' tag to: {item['name']}")
            
            # HDD tagging operations  
            for item in hdd_candidates:
                if item['action'] == 'add_hdd_tag':
                    logger.info(f"  🏷️  Add '{config.HDD_LOCATION_TAG}' tag to: {item['name']}")
            
            # Copy + tag operations
            for item in copy_operations:
                if item['action'] == 'copy_and_tag_hdd':
                    size_gb = "Unknown"
                    try:
                        # Try to get file/directory size
                        if os.path.exists(item['ssd_path']):
                            if os.path.isfile(item['ssd_path']):
                                size_bytes = os.path.getsize(item['ssd_path'])
                            else:
                                size_bytes = sum(os.path.getsize(os.path.join(dirpath, filename))
                                               for dirpath, dirnames, filenames in os.walk(item['ssd_path'])
                                               for filename in filenames)
                            size_gb = f"{size_bytes / (1024**3):.2f} GB"
                    except:
                        pass
                    
                    logger.info(f"  📁 Copy SSD→HDD + add '{config.HDD_LOCATION_TAG}' tag: {item['name']} ({size_gb})")
                    logger.info(f"     From: {item['ssd_path']}")
                    logger.info(f"     To:   {item['hdd_path']}")
            
            if untaggable:
                logger.info("⚠️  Untaggable torrents:")
                for item in untaggable[:10]:  # Show first 10
                    logger.info(f"  ? {item['name']}: {item['reason']}")
                if len(untaggable) > 10:
                    logger.info(f"  ... and {len(untaggable) - 10} more")
            
            return {
                'dry_run': True,
                'total_operations': total_operations,
                'ssd_operations': len(ssd_candidates),
                'hdd_operations': len(hdd_candidates),
                'copy_operations': len(copy_operations),
                'untaggable': len(untaggable)
            }
        
        # Perform actual operations (tagging + copying)
        successful_operations = 0
        failed_operations = 0
        
        logger.info("Performing enhanced tagging and copy operations...")
        
        # Process SSD tagging candidates
        for item in ssd_candidates:
            try:
                if item['action'] == 'add_ssd_tag':
                    client.torrents_add_tags(tags=config.SSD_LOCATION_TAG, torrent_hashes=item['hash'])
                    logger.debug(f"Added '{config.SSD_LOCATION_TAG}' tag to {item['name']}")
                    successful_operations += 1
            except Exception as e:
                logger.error(f"Failed to add SSD tag to {item['name']}: {e}")
                failed_operations += 1
        
        # Process HDD tagging candidates
        for item in hdd_candidates:
            try:
                if item['action'] == 'add_hdd_tag':
                    client.torrents_add_tags(tags=config.HDD_LOCATION_TAG, torrent_hashes=item['hash'])
                    logger.debug(f"Added '{config.HDD_LOCATION_TAG}' tag to {item['name']}")
                    successful_operations += 1
            except Exception as e:
                logger.error(f"Failed to add HDD tag to {item['name']}: {e}")
                failed_operations += 1
        
        # Process copy + tag operations
        for item in copy_operations:
            try:
                if item['action'] == 'copy_and_tag_hdd':
                    logger.info(f"📁 Copying {item['name']} from SSD to HDD...")
                    
                    # Determine if multi-file based on SSD path
                    is_multi_file = os.path.isdir(item['ssd_path'])
                    
                    # Ensure HDD base directory exists
                    hdd_base_dir = os.path.dirname(item['hdd_path'])
                    os.makedirs(hdd_base_dir, exist_ok=True)
                    
                    # Perform copy operation
                    copy_start_time = time.time()
                    try:
                        if is_multi_file:
                            shutil.copytree(item['ssd_path'], item['hdd_path'], copy_function=shutil.copy2, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item['ssd_path'], item['hdd_path'])
                        
                        copy_time = time.time() - copy_start_time
                        logger.info(f"   ✅ Copy completed in {copy_time:.1f}s")
                        
                        # Verify copy
                        if verify_copy(item['ssd_path'], item['hdd_path'], is_multi_file):
                            logger.info(f"   ✅ Copy verification successful")
                            
                            # Add HDD tag after successful copy
                            client.torrents_add_tags(tags=config.HDD_LOCATION_TAG, torrent_hashes=item['hash'])
                            logger.info(f"   🏷️  Added '{config.HDD_LOCATION_TAG}' tag to {item['name']}")
                            successful_operations += 1
                            
                        else:
                            logger.error(f"   ❌ Copy verification failed for {item['name']}")
                            # Clean up failed copy
                            try:
                                if os.path.exists(item['hdd_path']):
                                    if os.path.isdir(item['hdd_path']):
                                        shutil.rmtree(item['hdd_path'])
                                    else:
                                        os.remove(item['hdd_path'])
                            except:
                                pass
                            failed_operations += 1
                            
                    except Exception as copy_e:
                        logger.error(f"   ❌ Copy failed for {item['name']}: {copy_e}")
                        failed_operations += 1
                        
            except Exception as e:
                logger.error(f"Failed copy operation for {item['name']}: {e}")
                failed_operations += 1
        
        logger.info(f"Enhanced tagging complete: {successful_operations} successful, {failed_operations} failed")
        
        return {
            'dry_run': False,
            'total_operations': total_operations,
            'successful': successful_operations,
            'failed': failed_operations,
            'ssd_operations': len(ssd_candidates),
            'hdd_operations': len(hdd_candidates),
            'copy_operations': len(copy_operations),
            'untaggable': len(untaggable)
        }
        
    except Exception as e:
        logger.error(f"Error during torrent tagging: {e}")
        return {'error': str(e)}

def get_location_tag_summary(client: 'QBittorrentClient'):
    """
    Get a summary of current location tagging status.
    
    Returns:
        dict: Summary of tag distribution
    """
    import config
    
    if not config.ENABLE_LOCATION_TAGGING:
        return {'error': 'Location tagging disabled'}
    
    try:
        # Get torrents by tag
        ssd_torrents = get_torrents_by_tag(client, config.SSD_LOCATION_TAG)
        hdd_torrents = get_torrents_by_tag(client, config.HDD_LOCATION_TAG)
        all_torrents = get_all_torrents(client)
        
        # Calculate tag combinations
        ssd_hashes = {t.hash for t in ssd_torrents}
        hdd_hashes = {t.hash for t in hdd_torrents}
        
        # Count different categories
        ssd_only_count = 0      # Only on SSD
        hdd_only_count = 0      # Only on HDD  
        dual_location_count = 0 # On both SSD and HDD
        untagged_count = 0      # No location tags
        
        for torrent in all_torrents:
            has_ssd = torrent.hash in ssd_hashes
            has_hdd = torrent.hash in hdd_hashes
            
            if has_ssd and has_hdd:
                dual_location_count += 1
            elif has_ssd and not has_hdd:
                ssd_only_count += 1
            elif has_hdd and not has_ssd:
                hdd_only_count += 1
            else:
                untagged_count += 1
        
        return {
            'total_torrents': len(all_torrents),
            'ssd_only': ssd_only_count,
            'hdd_only': hdd_only_count,
            'dual_location': dual_location_count,
            'untagged': untagged_count,
            'ssd_tag': config.SSD_LOCATION_TAG,
            'hdd_tag': config.HDD_LOCATION_TAG
        }
        
    except Exception as e:
        logger.error(f"Error getting tag summary: {e}")
        return {'error': str(e)}

def auto_tag_torrent(client: 'QBittorrentClient', torrent_info, current_tags=''):
    """
    Auto-tag a torrent based on its location if auto-tagging is enabled.
    
    Args:
        client: qBittorrent client instance
        torrent_info: TorrentInfo object with torrent details
        current_tags: String of current tags for the torrent
        
    Returns:
        bool: True if tagging was successful or not needed, False if failed
    """
    import config
    
    if not config.ENABLE_LOCATION_TAGGING or not config.AUTO_TAG_NEW_TORRENTS:
        return True
    
    # Check if already has location tags
    has_ssd_tag = config.SSD_LOCATION_TAG in current_tags
    has_hdd_tag = config.HDD_LOCATION_TAG in current_tags
    
    if has_ssd_tag or has_hdd_tag:
        return True  # Already tagged
    
    # Determine location and add appropriate tag
    if torrent_info.path.startswith(config.DOWNLOAD_PATH_SSD):
        try:
            client.torrents_add_tags(tags=config.SSD_LOCATION_TAG, torrent_hashes=str(torrent_info.hash))
            logger.info(f"Auto-tagged torrent with '{config.SSD_LOCATION_TAG}' tag")
            return True
        except Exception as e:
            logger.warning(f"Failed to auto-tag torrent: {e}")
            return False
    
    return True  # No tagging needed for this location

def add_hdd_tag(client: 'QBittorrentClient', torrent_hash):
    """
    Add the HDD location tag to a torrent.
    
    Args:
        client: qBittorrent client instance
        torrent_hash: Hash of the torrent to tag
        
    Returns:
        bool: True if successful, False if failed
    """
    import config
    
    if not config.ENABLE_LOCATION_TAGGING:
        return True
    
    try:
        client.torrents_add_tags(tags=config.HDD_LOCATION_TAG, torrent_hashes=str(torrent_hash))
        logger.info(f"Added '{config.HDD_LOCATION_TAG}' tag - torrent now has dual-location tags")
        return True
    except Exception as e:
        logger.warning(f"Failed to add HDD location tag: {e}")
        return False

def remove_ssd_tag(client: 'QBittorrentClient', torrent_hash):
    """
    Remove the SSD location tag from a torrent.
    
    Args:
        client: qBittorrent client instance
        torrent_hash: Hash of the torrent to untag
        
    Returns:
        bool: True if successful, False if failed
    """
    import config
    
    if not config.ENABLE_LOCATION_TAGGING:
        return True
    
    try:
        client.torrents_remove_tags(tags=config.SSD_LOCATION_TAG, torrent_hashes=str(torrent_hash))
        logger.info(f"Removed '{config.SSD_LOCATION_TAG}' tag - torrent now exists only on HDD")
        return True
    except Exception as e:
        logger.warning(f"Failed to remove SSD location tag: {e}")
        return False 