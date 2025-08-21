#!/usr/bin/env python3

import os
import shutil
import time
import requests
import typing
from classes import TorrentInfo, BTIH, TimeoutError
from tags import add_hdd_tag, remove_ssd_tag
from qbit import (
    get_torrent_by_hash, get_torrents_by_status,
    get_torrents_by_status_and_tag
)
from util import (
    verify_copy, get_available_space_gb, cleanup_destination
)
# Import configuration constants
import config

# Import logging
try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-core')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-core')

if typing.TYPE_CHECKING:
    from qbittorrentapi import Client as QBittorrentClient

# ===================================================================
# Core Action Functions
# ===================================================================
def notify_arr_scan_downloads(service_type, download_id: 'BTIH', arr_config, hdd_path: str = None):
    """Notifies Sonarr or Radarr to scan for completed downloads using the command API.
    
    Args:
        service_type: Either 'sonarr' or 'radarr'
        download_id: Torrent hash for downloadClientId parameter
        arr_config: Configuration dictionary containing URLs and API keys
        hdd_path: Path where the movie/episode was moved to on HDD
    """
    if not arr_config.get("NOTIFY_ARR_ENABLED", False): 
        logger.info("Arr notification disabled, skipping.")
        return

    if service_type == "sonarr":
        base_url = arr_config.get("SONARR_URL", "").rstrip('/')
        api_key = arr_config.get("SONARR_API_KEY", "")
        service_name = "Sonarr"
        command_name = "DownloadedEpisodesScan"  # Sonarr equivalent
    elif service_type == "radarr":
        base_url = arr_config.get("RADARR_URL", "").rstrip('/')
        api_key = arr_config.get("RADARR_API_KEY", "")
        service_name = "Radarr"
        command_name = "DownloadedMoviesScan"  # Radarr command for scanning downloaded movies
    else: 
        logger.warning(f"Unknown service type '{service_type}' for notification.")
        return
        
    if not base_url or not api_key: 
        logger.warning(f"{service_name} URL or API Key not configured. Skipping notification.")
        return

    # Use the correct command API endpoint
    api_endpoint = f"{base_url}/api/v3/command"
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    # Prepare command payload with downloadClientId and path for targeted scanning
    payload = {
        "name": command_name,
        "downloadClientId": str(download_id)
    }
    
    # Add path parameter if provided for more targeted scanning
    if hdd_path:
        payload["path"] = hdd_path
        logger.info(f"Will scan specific path: {hdd_path}")
    
    if config.DRY_RUN:
        logger.info(f"[DRY RUN] Would notify {service_name} via POST {api_endpoint}")
        logger.info(f"[DRY RUN] Command: {payload}")
        return
    
    logger.info(f"Notifying {service_name} to scan for downloaded content...")
    logger.info(f"Sending command '{command_name}' with downloadClientId: {download_id}")
    if hdd_path:
        logger.info(f"Target path: {hdd_path}")
    
    try:
        response = requests.post(api_endpoint, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        
        if response.status_code in [200, 201, 202]:
            logger.info(f"{service_name} command sent successfully (Status: {response.status_code}).")
            try: 
                response_json = response.json()
                command_id = response_json.get('id', 'Unknown')
                logger.info(f"Command queued with ID: {command_id}")
                logger.debug(f"Response: {str(response_json)[:200]}...")
            except requests.exceptions.JSONDecodeError: 
                logger.debug(f"Response Text: {response.text[:200]}...")
        else: 
            logger.warning(f"{service_name} command returned unexpected status: {response.status_code}")
            logger.debug(f"Response: {response.text[:200]}...")
            
    except requests.exceptions.RequestException as e: 
        logger.error(f"ERROR notifying {service_name}: {e}")
    except Exception as e: 
        logger.error(f"Unexpected error during {service_name} notification: {e}")


def relocate_and_delete_ssd(client: 'QBittorrentClient', torrent_info: 'TorrentInfo', final_dest_base_hdd: str, download_path_ssd: str):
    """ Stops torrent, sets qBittorrent location to HDD path, deletes SSD copy, restarts. Uses qBittorrent API."""
    hdd_base_dir = os.path.join(final_dest_base_hdd, torrent_info.category)
    logger.info(f"Attempting relocation for {torrent_info.hash} ('{torrent_info.name}'):")
    logger.info(f"SSD path (to delete): {torrent_info.path}")
    logger.info(f"Target HDD base dir (for qBittorrent): {hdd_base_dir}")

    if config.DRY_RUN:
        logger.info(f"[DRY RUN] Would relocate torrent {torrent_info.hash} from SSD to HDD")
        logger.info(f"[DRY RUN] Would stop torrent, update directory to {hdd_base_dir}, delete {torrent_info.path}, restart torrent")
        return True

    was_started = False; start_successful = True; delete_successful = False

    try:
        # Get torrent info from qBittorrent
        try:
            torrent = get_torrent_by_hash(client, str(torrent_info.hash))
        except ValueError:
            logger.error(f"Torrent {torrent_info.hash} not found for relocation.")
            return False

        logger.info("Checking torrent state via qBittorrent API...")
        # Check if torrent is active (downloading or uploading)
        if torrent.state in ['downloading', 'uploading', 'stalledDL', 'stalledUP', 'queuedDL', 'queuedUP', 'checkingDL', 'checkingUP', 'forcedDL', 'forcedUP']:
            logger.info("Torrent is active. Pausing via qBittorrent API...")
            was_started = True
            client.torrents_pause(torrent_hashes=str(torrent_info.hash))
            logger.info("Pause command sent.")
            time.sleep(1)
        else: 
            logger.info("Torrent is already paused.")

        logger.info("Updating torrent location via qBittorrent API...")
        # In qBittorrent, we use set_location to move the torrent base directory
        client.torrents_set_location(location=hdd_base_dir, torrent_hashes=str(torrent_info.hash))
        logger.info("Successfully updated torrent location.")
        time.sleep(0.5)

        # CRITICAL: Verify destination exists before deleting source
        expected_hdd_path = os.path.join(hdd_base_dir, torrent_info.name.strip())
        logger.info(f"Verifying destination exists at: {expected_hdd_path}")
        
        if not os.path.exists(expected_hdd_path):
            logger.warning(f"Destination path '{expected_hdd_path}' does not exist!")
            logger.info(f"Need to copy data from SSD to HDD first...")
            
            if config.DRY_RUN:
                logger.info(f"[DRY RUN] Would copy {'directory' if torrent_info.is_multi_file else 'file'} from {torrent_info.path} to {expected_hdd_path}")
            else:
                try:
                    # Ensure base directory exists
                    os.makedirs(hdd_base_dir, exist_ok=True)
                    
                    copy_start_time = time.time()
                    if torrent_info.is_multi_file:
                        shutil.copytree(torrent_info.path, expected_hdd_path, copy_function=shutil.copy2, dirs_exist_ok=True)
                    else:
                        shutil.copy2(torrent_info.path, expected_hdd_path)
                    logger.info(f"Copy completed in {time.time() - copy_start_time:.2f} seconds.")
                    
                    # Verify the copy was successful
                    # Note: verify_copy already imported at top of file
                    if not verify_copy(torrent_info.path, expected_hdd_path, torrent_info.is_multi_file):
                        logger.error(f"Copy verification failed!")
                        if was_started: 
                            logger.info("Attempting to resume torrent after copy failure...")
                            client.torrents_resume(torrent_hashes=str(torrent_info.hash))
                        return False
                    logger.info(f"Copy verification successful.")
                    
                except (shutil.Error, OSError) as e:
                    logger.error(f"Failed to copy data to HDD: {e}")
                    if was_started: 
                        logger.info("Attempting to resume torrent after copy failure...")
                        client.torrents_resume(torrent_hashes=str(torrent_info.hash))
                    return False
        else:
            logger.info(f"Destination already exists on HDD.")

        logger.info(f"Destination verified. Proceeding to delete SSD data at: {torrent_info.path}")
        
        # Safety check before deletion
        try:
            norm_ssd_dl_path = os.path.normpath(os.path.realpath(download_path_ssd))
            norm_ssd_data_path = os.path.normpath(os.path.realpath(torrent_info.path))
            if os.path.commonpath([norm_ssd_data_path, norm_ssd_dl_path]) != norm_ssd_dl_path:
                logger.error(f"SAFETY ERROR: Path '{norm_ssd_data_path}' not within '{norm_ssd_dl_path}'. Aborting delete.")
                if was_started: 
                    logger.info("Attempting to resume torrent after safety check failure...")
                    client.torrents_resume(torrent_hashes=str(torrent_info.hash))
                return False
        except FileNotFoundError: 
            logger.warning(f"SSD path '{torrent_info.path}' not found for safety check.")
            delete_successful = True
        except Exception as e: 
            logger.error(f"Error during safety check: {e}")
            return False

        # Delete SSD data only if safety check passed/path already gone
        if not delete_successful:
            try:
                if os.path.exists(torrent_info.path):
                    if os.path.isdir(torrent_info.path): 
                        shutil.rmtree(torrent_info.path)
                        logger.info(f"Successfully deleted SSD directory.")
                    elif os.path.isfile(torrent_info.path): 
                        os.remove(torrent_info.path)
                        logger.info(f"Successfully deleted SSD file.")
                    delete_successful = True
                else: 
                    logger.warning(f"SSD path not found for deletion (already gone).")
                    delete_successful = True
            except OSError as e: 
                logger.error(f"Error deleting SSD data: {e}")
                delete_successful = False

        # Update location tags if tagging is enabled
        if delete_successful:
            remove_ssd_tag(client, torrent_info.hash)

        # Restart torrent if it was originally running
        if was_started:
            logger.info("Resuming torrent via qBittorrent API...")
            client.torrents_resume(torrent_hashes=str(torrent_info.hash))
            logger.info("Resume command sent.")
            start_successful = True # Assume success if no exception

        return delete_successful and start_successful

    except Exception as e:
        logger.error(f"qBittorrent API error during relocation of {torrent_info.hash}: {e}")
        if was_started and "resume" not in str(e).lower():
            try: 
                logger.info("Attempting to resume torrent after error...")
                client.torrents_resume(torrent_hashes=str(torrent_info.hash))
            except Exception as restart_e: 
                logger.error(f"Failed to send resume command after error: {restart_e}")
        return False
# ===================================================================

# ===================================================================
# Main Processing Functions (Orchestration Logic)
# ===================================================================
def process_single_torrent_optimized(client: 'QBittorrentClient', torrent_info: 'TorrentInfo'):
    """
    Optimized version that accepts TorrentInfo directly, avoiding expensive API calls.
    Used when called via qBittorrent's 'run on torrent finish' with parameters.
    """
    logger.info(f"--- Processing torrent (OPTIMIZED): {torrent_info.hash} ---")
    start_process_time = time.time()
    copy_verified = False

    # Extract needed variables (no API calls needed!)
    is_multi = torrent_info.is_multi_file
    ssd_data_path = torrent_info.path
    category = torrent_info.category

    # 2. Construct Paths using config paths
    hdd_base_dir = os.path.join(config.FINAL_DEST_BASE_HDD, category)
    hdd_data_path = os.path.join(hdd_base_dir, torrent_info.name.strip())
    logger.info(f"Source SSD Path: {ssd_data_path}")
    logger.info(f"Target HDD Path: {hdd_data_path}")
    logger.info(f"Torrent Category: {category}")
    logger.info(f"Multi-file: {is_multi} ({torrent_info.size / (1024**3):.2f} GB)")

    # 3. Pre-Copy Check: Handle existing destination from previous script run
    if os.path.exists(hdd_data_path):
        logger.warning(f"Destination path '{hdd_data_path}' already exists.")
        # Call verify_copy from util
        if verify_copy(ssd_data_path, hdd_data_path, is_multi):
            logger.info("Existing destination verified successfully. Skipping copy.")
            copy_verified = True # Treat existing verified copy as success
        else:
            logger.warning("Existing destination failed verification. Attempting cleanup and fresh copy.")
            cleanup_destination(hdd_data_path) # Call cleanup from util

    # 4. Copy & Verify Loop (if not already verified)
    if not copy_verified:
        # Use retry attempts from config
        max_attempts = max(1, config.COPY_RETRY_ATTEMPTS)
        for attempt in range(1, max_attempts + 1):
            logger.info(f"Copy attempt {attempt}/{max_attempts}...")

            # Clean up destination from *previous failed attempt within this loop*
            if attempt > 1 and os.path.exists(hdd_data_path):
                 logger.info("Cleaning up destination from previous failed attempt...")
                 cleanup_destination(hdd_data_path) # Call cleanup from util

            # Attempt Copy
            copy_succeeded_this_attempt = False
            try:
                # Ensure base directory exists before copy
                os.makedirs(hdd_base_dir, exist_ok=True)
                copy_start_time = time.time()
                
                if config.DRY_RUN:
                    logger.info(f"[DRY RUN] Would copy {'directory' if is_multi else 'file'} from {ssd_data_path} to {hdd_data_path}")
                    copy_succeeded_this_attempt = True
                else:
                    if is_multi:
                        shutil.copytree(ssd_data_path, hdd_data_path, copy_function=shutil.copy2, dirs_exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(hdd_data_path), exist_ok=True)
                        shutil.copy2(ssd_data_path, hdd_data_path)
                    logger.info(f"Copy finished in {time.time() - copy_start_time:.2f} seconds (Attempt {attempt}).")
                    copy_succeeded_this_attempt = True
            except (shutil.Error, OSError) as e:
                logger.error(f"Error during copy (Attempt {attempt}): {e}")

            # Attempt Verification (only if copy didn't raise exception)
            if copy_succeeded_this_attempt:
                # In dry-run mode, assume verification would pass
                if config.DRY_RUN:
                    logger.info(f"[DRY RUN] Would verify copy integrity")
                    copy_verified = True; break
                # Call verify_copy from util
                elif verify_copy(ssd_data_path, hdd_data_path, is_multi):
                    copy_verified = True; break # Success! Exit loop.
                else:
                    logger.warning(f"Verification failed on attempt {attempt}.") # Loop continues

            # If this was the last attempt and we still haven't verified, log failure
            if attempt == max_attempts and not copy_verified:
                logger.error(f"Failed to copy and verify after {max_attempts} attempts.")
                break

    # 5. Notification Phase (only if copy was successfully verified)
    if copy_verified:
        # Add HDD location tag while keeping SSD tag (dual-location tracking)
        add_hdd_tag(client, torrent_info.hash)
        
        if config.ENABLE_IMPORT_SCRIPT_MODE:
            # In import script mode, Sonarr/Radarr handle their own import via the script
            # We just need to ensure the background copy is complete
            logger.info("Copy successful and verified. Import script mode active.")
            
            # Experimental: Also notify Sonarr/Radarr if configured to do so
            # This might speed up detection of completed downloads
            if config.NOTIFY_ARR_IN_IMPORT_MODE:
                logger.info("Experimental: Also notifying Arr service for faster detection...")
                
                service_to_notify = None
                # Determine which service to notify based on tag (using config tags)
                if category.lower() == config.SONARR_TAG.lower(): service_to_notify = "sonarr"
                elif category.lower() == config.RADARR_TAG.lower(): service_to_notify = "radarr"
                else: logger.info(f"Tag '{category}' does not match Arr tags. Skipping notification.")

                # If a matching service was found, send the notification
                if service_to_notify:
                    # Call notify_arr_scan_downloads, passing the config dict
                    notify_arr_scan_downloads(service_to_notify, torrent_info.hash, config.ARR_CONFIG, ssd_data_path)
        else:
            # Normal mode - notify Sonarr/Radarr to scan for the completed download
            logger.info("Copy successful and verified. Notifying Arr service...")
            
            service_to_notify = None
            # Determine which service to notify based on tag (using config tags)
            if category.lower() == config.SONARR_TAG.lower(): service_to_notify = "sonarr"
            elif category.lower() == config.RADARR_TAG.lower(): service_to_notify = "radarr"
            else: logger.info(f"Tag '{category}' does not match Sonarr/Radarr tags. Skipping notification.")

            # If a matching service was found, send the notification
            if service_to_notify:
                # Call notify_arr_scan_downloads, passing the config dict
                notify_arr_scan_downloads(service_to_notify, torrent_info.hash, config.ARR_CONFIG, hdd_data_path)

        logger.info(f"--- Successfully processed (OPTIMIZED): {torrent_info.hash} ---")
        success = True
    else:
         logger.error(f"--- Failed processing (OPTIMIZED): {torrent_info.hash} ---")
         if os.path.exists(hdd_data_path): cleanup_destination(hdd_data_path) # Final cleanup
         success = False

    logger.info(f"--- Finished optimized processing for {torrent_info.hash} in {time.time() - start_process_time:.2f} seconds ---")
    return success


def manage_ssd_space(client: 'QBittorrentClient'):
    """Checks SSD space and relocates oldest completed torrents from SSD to HDD if needed.
    
    NOTE: With the new service architecture, concurrency control is handled by the orchestrator,
    so file-based locking is no longer needed.
    """
    logger.info("--- Checking SSD Space and Managing Older Torrents ---")
    # Use get_available_space_gb from util, passing config path
    available_gb = get_available_space_gb(config.DOWNLOAD_PATH_SSD)
    if available_gb is None: 
        logger.error("Could not check SSD space. Skipping management.")
        return

    logger.info(f"Available SSD space: {available_gb:.2f} GB. Threshold: {config.DISK_SPACE_THRESHOLD_GB} GB.")
    # Use threshold from config
    if available_gb >= config.DISK_SPACE_THRESHOLD_GB: 
        logger.info("SSD space sufficient. No cleanup needed.")
        return

    space_needed = config.DISK_SPACE_THRESHOLD_GB - available_gb
    logger.warning(f"SSD space below threshold. Need to free up {space_needed:.2f} GB.")
    logger.info("Finding completed torrents residing on SSD for potential relocation via qBittorrent API...")

    sorted_torrents_on_ssd = [] # List to hold info
    try:
        # Efficiently get only completed torrents with SSD tag (if tagging is enabled)
        # This significantly reduces API overhead by filtering at the server level
        if config.ENABLE_LOCATION_TAGGING:
            # Use qBittorrent's built-in filtering: completed torrents with SSD tag
            ssd_torrents = get_torrents_by_status_and_tag(
                client, 'completed', config.SSD_LOCATION_TAG
            )
            
            # Filter to only include torrents that have BOTH SSD and HDD tags
            # These are torrents that exist on both locations and are candidates for SSD cleanup
            dual_location_torrents = []
            for torrent in ssd_torrents:
                current_tags = getattr(torrent, 'tags', '') or ''
                has_hdd_tag = config.HDD_LOCATION_TAG in current_tags
                if has_hdd_tag:
                    dual_location_torrents.append(torrent)
            
            ssd_torrents = dual_location_torrents
            logger.info(f"Found {len(ssd_torrents)} completed torrents with both '{config.SSD_LOCATION_TAG}' and '{config.HDD_LOCATION_TAG}' tags (candidates for SSD cleanup)")
        else:
            # Fallback: get completed torrents and filter by path
            completed_torrents = get_torrents_by_status(client, 'completed')
            ssd_torrents = [
                t for t in completed_torrents 
                if t.content_path and t.content_path.startswith(config.DOWNLOAD_PATH_SSD)
            ]
            logger.info(f"Found {len(ssd_torrents)} completed torrents on SSD path (out of {len(completed_torrents)} total completed)")

        for torrent in ssd_torrents:
            try:
                # Torrent is already known to be complete and on SSD (from filtering above)
                torrent_path = torrent.content_path
                
                # Double-check path if not using tags (extra safety)
                if not config.ENABLE_LOCATION_TAGGING and not (torrent_path and torrent_path.startswith(config.DOWNLOAD_PATH_SSD)):
                    continue
                # Get completion timestamp - qBittorrent uses 'completion_on' or calculate from last_activity
                completed_timestamp = getattr(torrent, 'completion_on', None)
                if not completed_timestamp:
                    # Fallback: use last_activity or current time if not available
                    completed_timestamp = getattr(torrent, 'last_activity', None)
                    if not completed_timestamp:
                        # Final fallback: assume completed recently
                        completed_timestamp = int(time.time())
                
                # Convert to integer timestamp if it's not already
                if isinstance(completed_timestamp, str):
                    try:
                        completed_timestamp = int(completed_timestamp)
                    except ValueError:
                        completed_timestamp = int(time.time())
                
                if completed_timestamp <= 0:
                    logger.warning(f"Skipping torrent {torrent.hash} due to invalid completion timestamp")
                    continue
                
                # Create TorrentInfo object for relocation function efficiently
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
                info = {
                    "torrent_info": torrent_info,
                    "size": torrent.size/(1024**3),
                    "timestamp": completed_timestamp
                }
                sorted_torrents_on_ssd.append(info)
            except AttributeError as e: 
                logger.warning(f"Attribute error processing torrent {getattr(torrent, 'hash', 'UNKNOWN')}: {e}")
            except Exception as e: 
                logger.warning(f"Unexpected error processing torrent {getattr(torrent, 'hash', 'UNKNOWN')}: {e}")

        # Sort the collected list by timestamp (oldest first)
        sorted_torrents_on_ssd = sorted(sorted_torrents_on_ssd, key=lambda x: x['timestamp'])

    except Exception as e: 
        logger.error(f"Failed to get list of torrents for space management: {e}")
        return

    if not sorted_torrents_on_ssd: 
        logger.info("No eligible completed torrents found on SSD to relocate.")
        return
    logger.info(f"Found {len(sorted_torrents_on_ssd)} completed torrent(s) on SSD to consider for relocation (oldest first).")

    # Relocate Oldest Torrents until Space Threshold is Met
    space_freed_gb = 0; relocated_count = 0
    for info in sorted_torrents_on_ssd:
        if space_freed_gb >= space_needed: 
            logger.info(f"Successfully freed {space_freed_gb:.2f} GB.")
            break
        
        # Try import script mode relocation first, then fallback to normal relocation
        relocation_success = False
        
        if config.ENABLE_IMPORT_SCRIPT_MODE:
            success, reason = relocate_and_delete_ssd_import_script_mode(
                client, info["torrent_info"], config.FINAL_DEST_BASE_HDD, config.DOWNLOAD_PATH_SSD
            )
            
            if success:
                relocation_success = True
            elif reason in ["streaming", "no_symlinks", "no_links", "no_hdd_copy", "no_config"]:
                # These are "skip this torrent" conditions, not failures
                logger.info(f"Skipping torrent {info['torrent_info'].hash}: {reason}")
                continue  # Skip to next torrent without trying fallback
            else:
                # reason == "error" or other actual failure - try fallback
                logger.warning(f"Import script mode failed ({reason}), trying fallback for {info['torrent_info'].hash}")
                relocation_success = False
        
        # Fallback to normal relocation if import script mode failed with an error or is disabled
        if not relocation_success:
            relocation_success = relocate_and_delete_ssd(
                client, info["torrent_info"], config.FINAL_DEST_BASE_HDD, config.DOWNLOAD_PATH_SSD
            )
        
        if relocation_success:
            space_freed_gb += info["size"]; relocated_count += 1
        else: 
            logger.error(f"Stopping relocation process due to failure on {info['torrent_info'].hash}.")
            break

    logger.info(f"Space Management Summary: Relocated {relocated_count} older torrent(s), freeing approx {space_freed_gb:.2f} GB.")
    final_available_space = available_gb + space_freed_gb
    logger.info(f"Estimated available SSD space is now {final_available_space:.2f} GB.")

def relocate_and_delete_ssd_import_script_mode(client: 'QBittorrentClient', torrent_info: 'TorrentInfo', 
                                              final_dest_base_hdd: str, download_path_ssd: str) -> tuple[bool, str]:
    """
    Enhanced relocation function for import script mode with Tautulli streaming checks
    and symlink→hardlink replacement.
    
    Args:
        client: qBittorrent client
        torrent_info: Torrent information
        final_dest_base_hdd: HDD base directory
        download_path_ssd: SSD download path
    
    Returns:
        tuple[bool, str]: (success, reason)
        - (True, "success"): Successfully relocated and deleted
        - (False, "streaming"): Files currently streaming, skip this torrent
        - (False, "no_symlinks"): No symlinks found, skip this torrent  
        - (False, "no_hdd_copy"): HDD copy missing/invalid, skip this torrent
        - (False, "error"): Actual failure, could try fallback
    """
    if not config.ENABLE_IMPORT_SCRIPT_MODE:
        return False, "disabled"
    
    logger.info(f"--- Import script mode relocation for {torrent_info.hash} ---")
    
    try:
        # Step 1: Check if any files are currently streaming
        from tautulli import get_streaming_status_for_directory
        
        streaming_status = get_streaming_status_for_directory(
            torrent_info.path, 
            config.TAUTULLI_URL, 
            config.TAUTULLI_API_KEY
        )
        
        if streaming_status.get('is_any_file_streaming', False):
            streaming_files = streaming_status.get('streaming_files', [])
            logger.info(f"⏸️  Skipping relocation - files currently streaming: {len(streaming_files)} files")
            for file_path in streaming_files:
                logger.info(f"   📺 Streaming: {os.path.basename(file_path)}")
            return False, "streaming"
        
        logger.info("✅ No streaming activity detected, proceeding with relocation")
        
        # Step 2: Find symlinks that need to be replaced
        all_root_folders = []
        if config.SONARR_ROOT_FOLDERS:
            all_root_folders.extend(config.SONARR_ROOT_FOLDERS)
        if config.RADARR_ROOT_FOLDERS:
            all_root_folders.extend(config.RADARR_ROOT_FOLDERS)
        
        if not all_root_folders:
            logger.warning("No root folders configured for symlink discovery")
            return False, "no_config"
        
        # Find both symlinks and hardlinks pointing to this torrent's SSD/HDD paths
        # This provides backwards compatibility with existing hardlinks from old workflow
        from symlink_utils import find_links_to_ssd_path
        
        try:
            # Try using the find command first (more efficient)
            symlinks_to_replace, existing_hardlinks = find_links_to_ssd_path(
                torrent_info.path, expected_hdd_path, all_root_folders
            )
        except Exception as e:
            logger.warning(f"Find command failed, using fallback: {e}")
            # For now, fall back to old method (could implement Python version later)
            from symlink_utils import find_symlinks_to_ssd_path
            symlinks_to_replace = find_symlinks_to_ssd_path(torrent_info.path, all_root_folders)
            existing_hardlinks = []
        
        total_links = len(symlinks_to_replace) + len(existing_hardlinks)
        
        if total_links == 0:
            logger.warning(f"No symlinks or hardlinks found for torrent {torrent_info.hash}, skipping relocation")
            return False, "no_links"
        
        logger.info(f"Found {len(symlinks_to_replace)} symlink(s) and {len(existing_hardlinks)} existing hardlink(s)")
        
        # If we only have existing hardlinks (no symlinks), we can proceed directly to cleanup
        # since the content is already properly linked to HDD
        if symlinks_to_replace and not existing_hardlinks:
            logger.info("Found symlinks to replace with hardlinks")
        elif existing_hardlinks and not symlinks_to_replace:
            logger.info("Found existing hardlinks from previous workflow, proceeding to SSD cleanup")
        else:
            logger.info("Found both symlinks and hardlinks, proceeding with symlink replacement")
        
        # Step 3: Verify HDD copy exists and is valid
        hdd_base_dir = os.path.join(final_dest_base_hdd, torrent_info.category)
        expected_hdd_path = os.path.join(hdd_base_dir, torrent_info.name.strip())
        
        if not os.path.exists(expected_hdd_path):
            logger.warning(f"HDD copy doesn't exist at {expected_hdd_path}")
            logger.info("HDD copy missing, skipping import script mode relocation")
            return False, "no_hdd_copy"
        
        # Verify HDD copy integrity
        from util import verify_copy
        if not verify_copy(torrent_info.path, expected_hdd_path, torrent_info.is_multi_file):
            logger.error("HDD copy verification failed. Skipping import script mode relocation.")
            return False, "no_hdd_copy"
        
        logger.info("✅ HDD copy verified successfully")
        
        # Step 4: Replace symlinks with hardlinks (if any symlinks exist)
        replaced_count = 0
        
        if symlinks_to_replace:
            from symlink_utils import replace_symlinks_with_hardlinks
            
            replaced_count = replace_symlinks_with_hardlinks(
                symlinks_to_replace, 
                torrent_info.path, 
                expected_hdd_path
            )
            
            if replaced_count == 0:
                logger.error("Failed to replace any symlinks with hardlinks")
                return False, "error"
            
            logger.info(f"✅ Replaced {replaced_count} symlink(s) with hardlink(s)")
        else:
            logger.info("✅ No symlinks to replace - existing hardlinks are sufficient")
        
        # Step 5: Update torrent location in qBittorrent
        was_started = False
        try:
            # Get current torrent state
            torrent = get_torrent_by_hash(client, str(torrent_info.hash))
            
            # Pause if running
            if torrent.state in ['downloading', 'uploading', 'stalledDL', 'stalledUP', 'queuedDL', 'queuedUP', 'checkingDL', 'checkingUP', 'forcedDL', 'forcedUP']:
                logger.info("Pausing torrent for location update...")
                was_started = True
                client.torrents_pause(torrent_hashes=str(torrent_info.hash))
                time.sleep(1)
            
            # Update torrent location
            logger.info(f"Updating torrent location to: {hdd_base_dir}")
            client.torrents_set_location(location=hdd_base_dir, torrent_hashes=str(torrent_info.hash))
            time.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Failed to update torrent location: {e}")
            if was_started:
                try:
                    client.torrents_resume(torrent_hashes=str(torrent_info.hash))
                except:
                    pass
            return False, "error"
        
        # Step 6: Delete SSD data with safety checks
        deletion_success = False
        try:
            # Safety check - ensure path is within SSD download directory
            norm_ssd_dl_path = os.path.normpath(os.path.realpath(download_path_ssd))
            norm_ssd_data_path = os.path.normpath(os.path.realpath(torrent_info.path))
            
            if os.path.commonpath([norm_ssd_data_path, norm_ssd_dl_path]) != norm_ssd_dl_path:
                logger.error(f"SAFETY ERROR: Path '{norm_ssd_data_path}' not within '{norm_ssd_dl_path}'. Aborting delete.")
                return False, "error"
            
            # Delete SSD data
            if os.path.exists(torrent_info.path):
                if os.path.isdir(torrent_info.path):
                    shutil.rmtree(torrent_info.path)
                    logger.info(f"✅ Deleted SSD directory: {torrent_info.path}")
                else:
                    os.remove(torrent_info.path)
                    logger.info(f"✅ Deleted SSD file: {torrent_info.path}")
                deletion_success = True
            else:
                logger.info("SSD data already removed")
                deletion_success = True
                
        except OSError as e:
            logger.error(f"❌ Failed to delete SSD data: {e}")
            deletion_success = False
        
        # Step 7: Update tags and resume torrent
        if deletion_success:
            remove_ssd_tag(client, torrent_info.hash)
            
            if was_started:
                logger.info("Resuming torrent...")
                client.torrents_resume(torrent_hashes=str(torrent_info.hash))
        
        logger.info(f"--- Import script mode relocation completed for {torrent_info.hash} ---")
        return deletion_success, "success" if deletion_success else "error"
        
    except Exception as e:
        logger.error(f"Error in import script mode relocation: {e}")
        return False, "error"

# ===================================================================

