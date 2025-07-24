#!/usr/bin/env python3

import os
import shutil
import time
import typing

# Import classes
from classes import TorrentInfo, BTIH

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
# Helper Functions
# ===================================================================

def _convert_qbt_torrents_to_torrent_info(torrents, client=None) -> typing.List[TorrentInfo]:
    """
    Convert qBittorrent API torrent objects to TorrentInfo objects.
    
    Args:
        torrents: List of qBittorrent torrent objects
        client: qBittorrent client (for file count determination if needed)
        
    Returns:
        List of TorrentInfo objects
    """
    torrent_infos = []
    
    for torrent in torrents:
        try:
            # Determine file count efficiently
            files_count = 1  # Default assumption
            if client:
                try:
                    files_list = client.torrents_files(torrent_hash=torrent.hash)
                    files_count = len(files_list)
                except:
                    # Fallback - use default
                    pass
            
            # Convert torrent object to dictionary for the factory method
            torrent_dict = {
                'hash': torrent.hash,
                'name': torrent.name,
                'content_path': getattr(torrent, 'content_path', ''),
                'save_path': getattr(torrent, 'save_path', ''),
                'root_path': getattr(torrent, 'root_path', ''),
                'size': getattr(torrent, 'size', 0),
                'category': getattr(torrent, 'category', '') or '',
                'tags': getattr(torrent, 'tags', ''),
                'tracker': getattr(torrent, 'tracker', ''),
            }
            
            # Create TorrentInfo using the factory method
            torrent_info = TorrentInfo.from_qbittorrent_api(torrent_dict, files_count)
            torrent_infos.append(torrent_info)
            
        except Exception as e:
            logger.warning(f"Failed to convert torrent {getattr(torrent, 'hash', 'UNKNOWN')} to TorrentInfo: {e}")
            continue
    
    return torrent_infos

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
        # Get all torrents and convert to TorrentInfo objects
        raw_torrents = get_all_torrents(client)
        all_torrent_infos = _convert_qbt_torrents_to_torrent_info(raw_torrents, client)
        
        ssd_candidates = []
        hdd_candidates = []
        copy_operations = []
        untaggable = []
        
        for torrent_info in all_torrent_infos:
            try:
                if not torrent_info.content_path:
                    untaggable.append({
                        'hash': str(torrent_info.hash),
                        'name': torrent_info.name,
                        'reason': 'No content path available'
                    })
                    continue
                
                # Check current tags
                current_tags = torrent_info.tags or ''
                has_ssd_tag = config.SSD_LOCATION_TAG in current_tags
                has_hdd_tag = config.HDD_LOCATION_TAG in current_tags
                
                # Enhanced location analysis with dual-location support
                if torrent_info.content_path.startswith(config.DOWNLOAD_PATH_SSD):
                    # Torrent is currently pointing to SSD location
                    logger.debug(f"Analyzing SSD torrent: {torrent_info.name}")
                    
                    # Always ensure SSD tag is present
                    if not has_ssd_tag:
                        ssd_candidates.append({
                            'hash': str(torrent_info.hash),
                            'name': torrent_info.name,
                            'path': torrent_info.content_path,
                            'current_tags': current_tags,
                            'action': 'add_ssd_tag'
                        })
                    
                    # Check if HDD copy exists (determine expected HDD path)
                    try:
                        # Use category from TorrentInfo
                        torrent_category = torrent_info.category or ''
                        if torrent_category:
                            expected_hdd_base = os.path.join(config.FINAL_DEST_BASE_HDD, torrent_category)
                            expected_hdd_path = os.path.join(expected_hdd_base, torrent_info.name.strip())
                            
                            if os.path.exists(expected_hdd_path):
                                # HDD copy exists - ensure HDD tag is present
                                logger.debug(f"HDD copy found for {torrent_info.name}")
                                if not has_hdd_tag:
                                    hdd_candidates.append({
                                        'hash': str(torrent_info.hash),
                                        'name': torrent_info.name,
                                        'path': torrent_info.content_path,
                                        'hdd_path': expected_hdd_path,
                                        'current_tags': current_tags,
                                        'action': 'add_hdd_tag'
                                    })
                            else:
                                # HDD copy missing - needs copy operation
                                logger.debug(f"HDD copy MISSING for {torrent_info.name}")
                                copy_operations.append({
                                    'hash': str(torrent_info.hash),
                                    'name': torrent_info.name,
                                    'ssd_path': torrent_info.content_path,
                                    'hdd_path': expected_hdd_path,
                                    'category': torrent_category,
                                    'current_tags': current_tags,
                                    'size': torrent_info.size,
                                    'is_multi_file': torrent_info.is_multi_file,
                                    'action': 'copy_and_tag_hdd'
                                })
                        else:
                            logger.warning(f"Torrent {torrent_info.name} has no category - cannot determine HDD path")
                            untaggable.append({
                                'hash': str(torrent_info.hash),
                                'name': torrent_info.name,
                                'path': torrent_info.content_path,
                                'reason': 'No category - cannot determine HDD path'
                            })
                    except Exception as e:
                        logger.warning(f"Error checking HDD path for {torrent_info.name}: {e}")
                        untaggable.append({
                            'hash': str(torrent_info.hash),
                            'name': torrent_info.name,
                            'path': torrent_info.content_path,
                            'reason': f'Error checking HDD path: {e}'
                        })
                        
                elif torrent_info.content_path.startswith(config.FINAL_DEST_BASE_HDD):
                    # Torrent is currently pointing to HDD location
                    logger.debug(f"Analyzing HDD torrent: {torrent_info.name}")
                    if not has_hdd_tag:
                        hdd_candidates.append({
                            'hash': str(torrent_info.hash),
                            'name': torrent_info.name,
                            'path': torrent_info.content_path,
                            'current_tags': current_tags,
                            'action': 'add_hdd_tag'
                        })
                else:
                    untaggable.append({
                        'hash': str(torrent_info.hash),
                        'name': torrent_info.name,
                        'path': torrent_info.content_path,
                        'reason': 'Path not in SSD or HDD location'
                    })
                    
            except Exception as e:
                logger.warning(f"Error analyzing torrent {torrent_info.hash}: {e}")
                untaggable.append({
                    'hash': str(torrent_info.hash),
                    'name': torrent_info.name,
                    'reason': f'Analysis error: {e}'
                })
        
        # Summary
        total_operations = len(ssd_candidates) + len(hdd_candidates) + len(copy_operations)
        
        logger.info(f"Enhanced tagging analysis complete:")
        logger.info(f"  SSD tag operations: {len(ssd_candidates)}")
        logger.info(f"  HDD tag operations: {len(hdd_candidates)}")
        logger.info(f"  Copy + tag operations: {len(copy_operations)}")
        logger.info(f"  Untaggable torrents: {len(untaggable)}")

        # Print unique 'reasons' for untaggable torrents
        unique_reasons = list(set(item['reason'] for item in untaggable))
        logger.debug(f"  Unique reasons for untaggable torrents: {unique_reasons}")
        
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
                    size_gb = f"{item['size'] / (1024**3):.2f} GB" if item.get('size', 0) > 0 else "Unknown size"
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
                    
                    # Use is_multi_file from TorrentInfo data
                    is_multi_file = item.get('is_multi_file', os.path.isdir(item['ssd_path']))
                    
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
        # Get torrents by tag and convert to TorrentInfo objects
        raw_ssd_torrents = get_torrents_by_tag(client, config.SSD_LOCATION_TAG)
        raw_hdd_torrents = get_torrents_by_tag(client, config.HDD_LOCATION_TAG)
        raw_all_torrents = get_all_torrents(client)
        
        # Convert to TorrentInfo objects (faster without file count)
        ssd_torrent_infos = _convert_qbt_torrents_to_torrent_info(raw_ssd_torrents)
        hdd_torrent_infos = _convert_qbt_torrents_to_torrent_info(raw_hdd_torrents)
        all_torrent_infos = _convert_qbt_torrents_to_torrent_info(raw_all_torrents)
        
        # Calculate tag combinations using TorrentInfo hashes
        ssd_hashes = {str(t.hash) for t in ssd_torrent_infos}
        hdd_hashes = {str(t.hash) for t in hdd_torrent_infos}
        
        # Count different categories
        ssd_only_count = 0      # Only on SSD
        hdd_only_count = 0      # Only on HDD  
        dual_location_count = 0 # On both SSD and HDD
        untagged_count = 0      # No location tags
        
        for torrent_info in all_torrent_infos:
            torrent_hash_str = str(torrent_info.hash)
            has_ssd = torrent_hash_str in ssd_hashes
            has_hdd = torrent_hash_str in hdd_hashes
            
            if has_ssd and has_hdd:
                dual_location_count += 1
            elif has_ssd and not has_hdd:
                ssd_only_count += 1
            elif has_hdd and not has_ssd:
                hdd_only_count += 1
            else:
                untagged_count += 1
        
        return {
            'total_torrents': len(all_torrent_infos),
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

def auto_tag_torrent(client: 'QBittorrentClient', torrent_info: TorrentInfo, current_tags=''):
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
    
    # Determine location and add appropriate tag using TorrentInfo
    if torrent_info.content_path.startswith(config.DOWNLOAD_PATH_SSD):
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
        torrent_hash: Hash of the torrent to tag (can be BTIH or string)
        
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
        torrent_hash: Hash of the torrent to untag (can be BTIH or string)
        
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