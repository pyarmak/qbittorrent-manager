#!/usr/bin/env python3
"""
Symlink Utilities for qBittorrent Manager Dual Path Mode

This module provides utilities for creating, managing, and replacing symlinks
with hardlinks in the dual path mode workflow.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional, List

# Import logging
try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-symlinks')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-symlinks')

# ===================================================================
# Symlink Management Functions
# ===================================================================

def create_symlink(source_path: str, target_path: str, is_multi_file: bool) -> bool:
    """
    Create symlink from SSD source to target location for immediate availability
    
    Args:
        source_path: Path to file/directory on SSD
        target_path: Path where symlink should be created
        is_multi_file: Whether this is a directory (True) or file (False)
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Ensure target directory exists
        target_dir = os.path.dirname(target_path)
        os.makedirs(target_dir, exist_ok=True)
        
        # Remove existing target if it exists
        if os.path.exists(target_path) or os.path.islink(target_path):
            if os.path.isdir(target_path) and not os.path.islink(target_path):
                shutil.rmtree(target_path)
            else:
                os.unlink(target_path)
        
        # Create symlink
        os.symlink(source_path, target_path)
        
        # Verify symlink was created correctly
        if not os.path.islink(target_path):
            logger.error(f"Failed to create symlink: {target_path} is not a symbolic link")
            return False
        
        # Verify symlink points to correct location
        if os.readlink(target_path) != source_path:
            logger.error(f"Symlink points to wrong location: {os.readlink(target_path)} != {source_path}")
            return False
        
        # Verify source is accessible through symlink
        if not os.path.exists(target_path):
            logger.error(f"Source not accessible through symlink: {source_path}")
            return False
        
        logger.info(f"✅ Created symlink: {target_path} -> {source_path}")
        return True
        
    except OSError as e:
        logger.error(f"❌ Failed to create symlink {target_path} -> {source_path}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error creating symlink {target_path} -> {source_path}: {e}")
        return False

def replace_symlink_with_hardlink(symlink_path: str, hdd_source_path: str, is_multi_file: bool) -> bool:
    """
    Replace symlink with hardlink to HDD copy

    The replacement is staged next to the symlink and swapped in only once it
    exists, so a failure (different filesystem, permissions) leaves the original
    symlink in place instead of removing it and leaving nothing behind.

    Args:
        symlink_path: Path to existing symlink
        hdd_source_path: Path to HDD copy to hardlink to
        is_multi_file: Whether this is a directory (True) or file (False)
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Verify inputs
        if not os.path.islink(symlink_path):
            logger.error(f"Target is not a symlink: {symlink_path}")
            return False
        
        if not os.path.exists(hdd_source_path):
            logger.error(f"HDD source does not exist: {hdd_source_path}")
            return False
        
        if is_multi_file:
            # For directories, we need to recreate the directory structure with hardlinks
            return _replace_directory_with_hardlinks(symlink_path, hdd_source_path, True)

        # For single files, stage the hardlink then swap it over the symlink
        staging_path = f"{symlink_path}.tmp_hardlink"
        if os.path.lexists(staging_path):
            os.unlink(staging_path)

        try:
            os.link(hdd_source_path, staging_path)
            os.replace(staging_path, symlink_path)
        except OSError:
            if os.path.lexists(staging_path):
                try:
                    os.unlink(staging_path)
                except OSError:
                    pass
            raise

        logger.info(f"✅ Replaced symlink with hardlink: {symlink_path} -> {hdd_source_path}")
        return True
        
    except OSError as e:
        logger.error(f"❌ Failed to replace symlink with hardlink {symlink_path}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error replacing symlink {symlink_path}: {e}")
        return False

def _create_hardlink_directory_tree(source_dir: str, target_dir: str) -> bool:
    """
    Create a directory tree with hardlinks for all files
    
    Args:
        source_dir: Source directory on HDD
        target_dir: Target directory path
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # A missing or empty source would otherwise produce an empty tree and
        # report success, which is how a replaced library folder could end up
        # holding nothing at all.
        if not os.path.isdir(source_dir):
            logger.error(f"❌ Hardlink source directory does not exist: {source_dir}")
            return False

        # Create target directory
        os.makedirs(target_dir, exist_ok=True)
        
        linked_files = 0
        # Walk through source directory and create hardlinks
        for root, dirs, files in os.walk(source_dir):
            # Calculate relative path from source root
            rel_path = os.path.relpath(root, source_dir)
            target_root = os.path.join(target_dir, rel_path) if rel_path != '.' else target_dir
            
            # Create subdirectories
            for dir_name in dirs:
                target_subdir = os.path.join(target_root, dir_name)
                os.makedirs(target_subdir, exist_ok=True)
            
            # Create hardlinks for files
            for file_name in files:
                source_file = os.path.join(root, file_name)
                target_file = os.path.join(target_root, file_name)
                
                # Remove existing file if it exists
                if os.path.lexists(target_file):
                    os.unlink(target_file)
                
                # Create hardlink
                os.link(source_file, target_file)
                linked_files += 1
        
        if linked_files == 0:
            logger.error(f"❌ Hardlink source directory contains no files: {source_dir}")
            return False

        logger.info(f"✅ Replaced symlink directory with hardlink tree ({linked_files} file(s)): {target_dir} -> {source_dir}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to create hardlink directory tree: {e}")
        return False

def is_symlink_target(path: str) -> bool:
    """Check if a path is a symlink"""
    return os.path.islink(path)

def get_symlink_target(path: str) -> Optional[str]:
    """Get the target of a symlink"""
    try:
        if os.path.islink(path):
            return os.readlink(path)
        return None
    except OSError:
        return None

def cleanup_broken_symlinks(directory: str) -> int:
    """
    Remove broken symlinks from a directory
    
    Args:
        directory: Directory to clean up
    
    Returns:
        int: Number of broken symlinks removed
    """
    removed_count = 0
    
    try:
        for root, dirs, files in os.walk(directory):
            # Check files
            for file_name in files:
                file_path = os.path.join(root, file_name)
                if os.path.islink(file_path) and not os.path.exists(file_path):
                    try:
                        os.unlink(file_path)
                        removed_count += 1
                        logger.info(f"Removed broken symlink: {file_path}")
                    except OSError as e:
                        logger.warning(f"Failed to remove broken symlink {file_path}: {e}")
            
            # Check directories (need to check separately as broken symlinks to directories
            # don't show up in files list)
            for dir_name in dirs[:]:  # Use slice copy to allow modification during iteration
                dir_path = os.path.join(root, dir_name)
                if os.path.islink(dir_path) and not os.path.exists(dir_path):
                    try:
                        os.unlink(dir_path)
                        dirs.remove(dir_name)  # Don't recurse into removed directory
                        removed_count += 1
                        logger.info(f"Removed broken symlink directory: {dir_path}")
                    except OSError as e:
                        logger.warning(f"Failed to remove broken symlink directory {dir_path}: {e}")
        
        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} broken symlinks from {directory}")
        
        return removed_count
        
    except Exception as e:
        logger.error(f"Error cleaning up broken symlinks in {directory}: {e}")
        return 0

# ===================================================================
# Symlink Discovery Functions (for Import Script Mode)
# ===================================================================

def find_links_to_ssd_path(ssd_path: str, hdd_path: str, search_directories: List[str]) -> tuple[List[str], List[str]]:
    """
    Find all symlinks AND hardlinks in search directories that point to SSD or corresponding HDD files
    This provides backwards compatibility with existing hardlinks from the old workflow.
    
    Args:
        ssd_path: SSD path to search for (can be file or directory)
        hdd_path: Corresponding HDD path (for hardlink detection)
        search_directories: List of directories to search for links
    
    Returns:
        tuple[List[str], List[str]]: (symlinks_to_ssd, hardlinks_to_hdd)
    """
    symlinks_found = []
    hardlinks_found = []
    
    if not search_directories:
        logger.warning("No search directories provided for link discovery")
        return symlinks_found, hardlinks_found
    
    # Normalize paths for comparison
    ssd_path_normalized = os.path.normpath(os.path.abspath(ssd_path))
    hdd_path_normalized = os.path.normpath(os.path.abspath(hdd_path))
    
    for search_dir in search_directories:
        if not os.path.exists(search_dir):
            logger.warning(f"Search directory does not exist: {search_dir}")
            continue
        
        logger.debug(f"Searching for links in: {search_dir}")
        
        try:
            # Use find command for efficiency
            result = subprocess.run([
                'find', search_dir, '-type', 'f', '-o', '-type', 'l'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.warning(f"Find command failed for {search_dir}: {result.stderr}")
                continue
            
            # Check each file/link to see if it points to our SSD/HDD path
            for file_path in result.stdout.strip().split('\n'):
                if not file_path:  # Skip empty lines
                    continue
                
                try:
                    if os.path.islink(file_path):
                        # Handle symlinks
                        target_path = os.readlink(file_path)
                        
                        # Convert relative links to absolute paths
                        if not os.path.isabs(target_path):
                            target_path = os.path.join(os.path.dirname(file_path), target_path)
                        
                        target_path_normalized = os.path.normpath(os.path.abspath(target_path))
                        
                        # Check if the symlink target is within our SSD path
                        if _is_path_within_directory(target_path_normalized, ssd_path_normalized):
                            symlinks_found.append(file_path)
                            logger.debug(f"Found symlink: {file_path} -> {target_path}")
                    
                    elif os.path.isfile(file_path):
                        # Handle regular files - check if they're hardlinks to HDD files
                        if _is_hardlink_to_hdd_path(file_path, hdd_path_normalized):
                            hardlinks_found.append(file_path)
                            logger.debug(f"Found hardlink to HDD: {file_path}")
                
                except (OSError, ValueError) as e:
                    logger.debug(f"Error checking file {file_path}: {e}")
                    continue
        
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout while searching for links in {search_dir}")
        except Exception as e:
            logger.error(f"Error searching for links in {search_dir}: {e}")
    
    logger.info(f"Found {len(symlinks_found)} symlink(s) and {len(hardlinks_found)} hardlink(s) for path: {ssd_path}")
    return symlinks_found, hardlinks_found

def find_symlinks_to_ssd_path(ssd_path: str, search_directories: List[str]) -> List[str]:
    """
    DEPRECATED: Use find_links_to_ssd_path instead for backwards compatibility
    Find all symlinks in search directories that point to files within the SSD path
    
    Args:
        ssd_path: SSD path to search for (can be file or directory)
        search_directories: List of directories to search for symlinks
    
    Returns:
        List[str]: List of symlink paths that point to the SSD path
    """
    symlinks_found = []
    
    if not search_directories:
        logger.warning("No search directories provided for symlink discovery")
        return symlinks_found
    
    # Normalize the SSD path for comparison
    ssd_path_normalized = os.path.normpath(os.path.abspath(ssd_path))
    
    for search_dir in search_directories:
        if not os.path.exists(search_dir):
            logger.warning(f"Search directory does not exist: {search_dir}")
            continue
        
        logger.debug(f"Searching for symlinks in: {search_dir}")
        
        try:
            # Use find command for efficiency (similar to user's example)
            # Find all symlinks in the search directory
            result = subprocess.run([
                'find', search_dir, '-type', 'l'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.warning(f"Find command failed for {search_dir}: {result.stderr}")
                continue
            
            # Check each symlink to see if it points to our SSD path
            for symlink_path in result.stdout.strip().split('\n'):
                if not symlink_path:  # Skip empty lines
                    continue
                
                try:
                    # Get the target of the symlink
                    target_path = os.readlink(symlink_path)
                    
                    # Convert relative links to absolute paths
                    if not os.path.isabs(target_path):
                        target_path = os.path.join(os.path.dirname(symlink_path), target_path)
                    
                    target_path_normalized = os.path.normpath(os.path.abspath(target_path))
                    
                    # Check if the symlink target is within our SSD path
                    if _is_path_within_directory(target_path_normalized, ssd_path_normalized):
                        symlinks_found.append(symlink_path)
                        logger.debug(f"Found symlink: {symlink_path} -> {target_path}")
                
                except (OSError, ValueError) as e:
                    logger.debug(f"Error checking symlink {symlink_path}: {e}")
                    continue
        
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout while searching for symlinks in {search_dir}")
        except Exception as e:
            logger.error(f"Error searching for symlinks in {search_dir}: {e}")
    
    logger.info(f"Found {len(symlinks_found)} symlink(s) pointing to SSD path: {ssd_path}")
    return symlinks_found

def find_symlinks_to_ssd_path_python(ssd_path: str, search_directories: List[str]) -> List[str]:
    """
    Pure Python implementation of symlink discovery (fallback if 'find' command is not available)
    
    Args:
        ssd_path: SSD path to search for (can be file or directory)  
        search_directories: List of directories to search for symlinks
    
    Returns:
        List[str]: List of symlink paths that point to the SSD path
    """
    symlinks_found = []
    
    if not search_directories:
        logger.warning("No search directories provided for symlink discovery")
        return symlinks_found
    
    # Normalize the SSD path for comparison
    ssd_path_normalized = os.path.normpath(os.path.abspath(ssd_path))
    
    for search_dir in search_directories:
        if not os.path.exists(search_dir):
            logger.warning(f"Search directory does not exist: {search_dir}")
            continue
        
        logger.debug(f"Searching for symlinks in: {search_dir} (Python implementation)")
        
        try:
            # Walk through the directory tree
            for root, dirs, files in os.walk(search_dir):
                # Check files for symlinks
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    if os.path.islink(file_path):
                        try:
                            # Get the target of the symlink
                            target_path = os.readlink(file_path)
                            
                            # Convert relative links to absolute paths
                            if not os.path.isabs(target_path):
                                target_path = os.path.join(os.path.dirname(file_path), target_path)
                            
                            target_path_normalized = os.path.normpath(os.path.abspath(target_path))
                            
                            # Check if the symlink target is within our SSD path
                            if _is_path_within_directory(target_path_normalized, ssd_path_normalized):
                                symlinks_found.append(file_path)
                                logger.debug(f"Found symlink: {file_path} -> {target_path}")
                        
                        except (OSError, ValueError) as e:
                            logger.debug(f"Error checking symlink {file_path}: {e}")
                            continue
                
                # Check directories for symlinks (symlinked directories)
                for dir_name in dirs[:]:  # Use slice to allow modification during iteration
                    dir_path = os.path.join(root, dir_name)
                    if os.path.islink(dir_path):
                        try:
                            # Get the target of the symlink
                            target_path = os.readlink(dir_path)
                            
                            # Convert relative links to absolute paths
                            if not os.path.isabs(target_path):
                                target_path = os.path.join(os.path.dirname(dir_path), target_path)
                            
                            target_path_normalized = os.path.normpath(os.path.abspath(target_path))
                            
                            # Check if the symlink target is within our SSD path
                            if _is_path_within_directory(target_path_normalized, ssd_path_normalized):
                                symlinks_found.append(dir_path)
                                logger.debug(f"Found directory symlink: {dir_path} -> {target_path}")
                                
                                # Don't recurse into symlinked directories
                                dirs.remove(dir_name)
                        
                        except (OSError, ValueError) as e:
                            logger.debug(f"Error checking directory symlink {dir_path}: {e}")
                            continue
        
        except Exception as e:
            logger.error(f"Error searching for symlinks in {search_dir}: {e}")
    
    logger.info(f"Found {len(symlinks_found)} symlink(s) pointing to SSD path: {ssd_path}")
    return symlinks_found

def _is_path_within_directory(file_path: str, directory_path: str) -> bool:
    """
    Check if a file path is within a directory path
    
    Args:
        file_path: File path to check
        directory_path: Directory path to check against
    
    Returns:
        bool: True if file_path is within directory_path
    """
    try:
        # For exact matches
        if file_path == directory_path:
            return True
        
        # For directory containment
        rel_path = os.path.relpath(file_path, directory_path)
        return not rel_path.startswith('..')
    except ValueError:
        # Different drives on Windows
        return False

def _is_hardlink_to_hdd_path(file_path: str, hdd_path_normalized: str) -> bool:
    """
    Check if a file is a hardlink to a file within the HDD path
    
    Args:
        file_path: File to check
        hdd_path_normalized: Normalized HDD path to check against
    
    Returns:
        bool: True if the file is a hardlink to something in the HDD path
    """
    try:
        if not os.path.exists(file_path):
            return False
        
        # Get file stats
        file_stat = os.stat(file_path)
        
        # If hardlink count is 1, it's not a hardlink
        if file_stat.st_nlink <= 1:
            return False
        
        # For hardlink detection, we need to check if there's a corresponding file
        # in the HDD directory structure with the same inode
        
        # Calculate relative path of the file from its search directory
        # and check if corresponding HDD file exists with same inode
        
        # The HDD path is a single file for torrents whose content is one file
        # (including a single file inside a root folder), so compare directly
        # instead of walking it as a directory.
        if os.path.isfile(hdd_path_normalized):
            try:
                hdd_stat = os.stat(hdd_path_normalized)
                if (file_stat.st_dev == hdd_stat.st_dev and
                        file_stat.st_ino == hdd_stat.st_ino and
                        file_path != hdd_path_normalized):
                    logger.debug(f"Found hardlink: {file_path} <-> {hdd_path_normalized}")
                    return True
            except (OSError, ValueError):
                pass
            return False

        # Walk through HDD directory to find files with same inode
        for root, dirs, files in os.walk(hdd_path_normalized):
            for filename in files:
                hdd_file_path = os.path.join(root, filename)
                try:
                    hdd_stat = os.stat(hdd_file_path)
                    
                    # Check if same device and inode (indicates hardlink)
                    if (file_stat.st_dev == hdd_stat.st_dev and 
                        file_stat.st_ino == hdd_stat.st_ino and
                        file_path != hdd_file_path):  # Different paths but same inode
                        logger.debug(f"Found hardlink: {file_path} <-> {hdd_file_path}")
                        return True
                except (OSError, ValueError):
                    continue
        
        return False
        
    except Exception as e:
        logger.debug(f"Error checking hardlink for {file_path}: {e}")
        return False

def replace_symlinks_with_hardlinks(symlink_paths: List[str], ssd_path: str, hdd_path: str) -> int:
    """
    Replace symlinks (and other non-HDD links) with hardlinks to HDD files.
    
    This function handles path translation between different directory structures
    and can replace:
    - Symlinks to SSD
    - Hardlinks to SSD  
    - Regular files (copies)
    
    For example, if the file is at /downloads/radarr/Movie/file.mkv and points to
    /downloading/radarr/<torrent>/file.mkv (SSD), it will be replaced with a
    hardlink to /downloads/flood/radarr/<torrent>/file.mkv (HDD).
    
    Args:
        symlink_paths: List of file paths to replace (from arr's library)
        ssd_path: Original SSD path where torrent was downloaded (e.g., /downloading/radarr/<torrent>)
        hdd_path: HDD path where files were copied (e.g., /downloads/flood/radarr/<torrent>)
    
    Returns:
        int: Number of files successfully replaced
    """
    replaced_count = 0
    
    if not symlink_paths:
        logger.debug("No files to replace")
        return replaced_count
    
    # Normalize paths for comparison
    ssd_path_normalized = os.path.normpath(os.path.abspath(ssd_path))
    hdd_path_normalized = os.path.normpath(os.path.abspath(hdd_path))
    
    logger.info(f"Replacing {len(symlink_paths)} file(s) with hardlinks to HDD")
    logger.debug(f"SSD torrent path: {ssd_path_normalized}")
    logger.debug(f"HDD torrent path: {hdd_path_normalized}")
    
    for file_path in symlink_paths:
        try:
            if not os.path.exists(file_path):
                logger.warning(f"Path does not exist, skipping: {file_path}")
                continue
            
            # Determine what type of file this is
            is_symlink = os.path.islink(file_path)
            
            if is_symlink:
                # Get the current target of the symlink (should point to SSD)
                current_target = os.readlink(file_path)
                
                # Convert relative target to absolute
                if not os.path.isabs(current_target):
                    current_target = os.path.join(os.path.dirname(file_path), current_target)
                
                current_target_normalized = os.path.normpath(os.path.abspath(current_target))
                
                logger.debug(f"Processing symlink: {file_path}")
                logger.debug(f"  Current target (SSD): {current_target_normalized}")
            else:
                # Not a symlink - could be hardlink to another location or regular file (copy)
                # We'll use the file's location to determine the corresponding HDD path
                logger.debug(f"Processing non-symlink file: {file_path}")
                
                # For non-symlinks, we need to figure out which file in the torrent it corresponds to
                # Strategy: Use the filename and try to find it in the SSD torrent path
                file_basename = os.path.basename(file_path)
                
                # Try to find the file in SSD path
                current_target_normalized = None
                if os.path.isfile(ssd_path_normalized):
                    # SSD path is a file - check if basename matches
                    if os.path.basename(ssd_path_normalized) == file_basename:
                        current_target_normalized = ssd_path_normalized
                else:
                    # SSD path is a directory - search for the file
                    # For efficiency, try to match the relative path structure first
                    # Extract the relative path from arr's library structure
                    # Example: /downloads/sonarr/Show/Season 2/episode.mkv
                    #          We want to find: /downloading/sonarr/<torrent>/episode.mkv
                    
                    # Walk the SSD torrent directory to find matching file
                    for root, dirs, files in os.walk(ssd_path_normalized):
                        if file_basename in files:
                            current_target_normalized = os.path.join(root, file_basename)
                            break
                
                if not current_target_normalized:
                    logger.warning(f"Cannot find corresponding SSD file for: {file_path}")
                    logger.debug(f"  Searched in: {ssd_path_normalized}")
                    logger.debug(f"  Looking for filename: {file_basename}")
                    continue
                
                logger.debug(f"  Corresponding SSD file: {current_target_normalized}")
            
            # Calculate the relative path within the torrent
            # This is the path from the torrent root to the specific file
            try:
                rel_path = os.path.relpath(current_target_normalized, ssd_path_normalized)
                if rel_path.startswith('..'):
                    logger.warning(f"File is not within SSD torrent path, skipping: {file_path}")
                    logger.debug(f"  Target: {current_target_normalized}")
                    logger.debug(f"  SSD base: {ssd_path_normalized}")
                    continue
                
                # Construct the corresponding HDD path
                # This maps the file from SSD torrent location to HDD torrent location
                if rel_path == '.':
                    # File is the torrent itself
                    hdd_target_path = hdd_path_normalized
                else:
                    # File is within the torrent
                    hdd_target_path = os.path.join(hdd_path_normalized, rel_path)
                
                logger.debug(f"  Relative path within torrent: {rel_path}")
                logger.debug(f"  Corresponding HDD path: {hdd_target_path}")
                
            except ValueError as e:
                logger.warning(f"Cannot calculate relative path for file: {file_path}")
                logger.debug(f"  Error: {e}")
                continue
            
            # Verify the HDD target exists
            if not os.path.exists(hdd_target_path):
                logger.error(f"HDD target does not exist, skipping: {hdd_target_path}")
                logger.debug(f"  File: {file_path}")
                logger.debug(f"  Expected HDD file: {hdd_target_path}")
                continue
            
            # Replace file with hardlink to HDD
            if _replace_file_with_hardlink(file_path, hdd_target_path, is_symlink):
                replaced_count += 1
                logger.info(f"✅ Replaced file with hardlink: {file_path}")
                logger.debug(f"  Now hardlinked to: {hdd_target_path}")
            else:
                logger.error(f"❌ Failed to replace file: {file_path}")
        
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")
            logger.debug(f"  Exception details: {type(e).__name__}: {str(e)}")
            continue
    
    logger.info(f"Successfully replaced {replaced_count}/{len(symlink_paths)} file(s) with hardlinks")
    return replaced_count


def _replace_file_with_hardlink(file_path: str, hdd_target_path: str, is_symlink: bool) -> bool:
    """
    Replace a file (symlink, hardlink, or regular file) with a hardlink to HDD.
    
    This function handles all types of files that need to be replaced:
    - Symlinks to SSD (created by import script)
    - Regular files (copies created by arr when import script didn't run)
    - Hardlinks to other locations on the same filesystem (arr's internal hardlinks)
    
    Note: Hardlinks between SSD and HDD are impossible since they're different filesystems.
    Any hardlinks found must be within the same filesystem (e.g., within /downloads).
    
    Args:
        file_path: Path to the file to replace
        hdd_target_path: Path to the HDD file to hardlink to
        is_symlink: Whether the file is a symlink (optimization hint)
    
    Returns:
        bool: True if successful
    """
    try:
        # Verify HDD target exists
        if not os.path.exists(hdd_target_path):
            logger.error(f"HDD target does not exist: {hdd_target_path}")
            return False
        
        # Check if HDD target is a directory
        is_directory = os.path.isdir(hdd_target_path)
        
        if is_directory:
            # For directories, we need to handle this differently
            # Remove the file/symlink and create a directory structure with hardlinks
            return _replace_directory_with_hardlinks(file_path, hdd_target_path, is_symlink)
        else:
            # For files, create a simple hardlink
            # Log what type of file we're replacing
            if is_symlink:
                logger.debug(f"Replacing symlink with hardlink: {file_path}")
            else:
                # Check if it's a hardlink or regular file
                try:
                    file_stat = os.stat(file_path)
                    if file_stat.st_nlink > 1:
                        logger.debug(f"Replacing hardlink (link count: {file_stat.st_nlink}) with hardlink to HDD: {file_path}")
                    else:
                        logger.debug(f"Replacing regular file (copy) with hardlink to HDD: {file_path}")
                except:
                    logger.debug(f"Replacing file with hardlink to HDD: {file_path}")
            
            # Create temporary hardlink first for atomic replacement
            temp_hardlink = f"{file_path}.tmp_hardlink"
            
            try:
                # Create hardlink to HDD file
                os.link(hdd_target_path, temp_hardlink)
                
                # Atomically replace file with hardlink
                # This works for symlinks, hardlinks, and regular files
                os.replace(temp_hardlink, file_path)
                
                logger.debug(f"Successfully created hardlink: {file_path} -> {hdd_target_path}")
                return True
                
            except OSError as e:
                # Clean up temp file if it exists
                if os.path.exists(temp_hardlink):
                    try:
                        os.unlink(temp_hardlink)
                    except:
                        pass
                logger.error(f"Failed to create hardlink: {e}")
                return False
    
    except Exception as e:
        logger.error(f"Unexpected error replacing file: {e}")
        return False


def _replace_directory_with_hardlinks(dir_path: str, hdd_dir_path: str, is_symlink: bool) -> bool:
    """
    Replace a directory (symlink or regular directory) with a directory containing hardlinks to all files.

    The replacement tree is built alongside the original and only swapped in once it
    is complete. Removing the original first would destroy the library copy whenever
    the hardlinks cannot be created (different filesystem, permissions, partial
    failure), leaving nothing behind.

    Args:
        dir_path: Path to the directory to replace
        hdd_dir_path: Path to the HDD directory
        is_symlink: Whether the directory is a symlink (optimization hint)
    
    Returns:
        bool: True if successful
    """
    staging_path = f"{dir_path}.tmp_hardlink"
    try:
        # Clear any leftover staging directory from an interrupted run
        if os.path.lexists(staging_path):
            if os.path.islink(staging_path):
                os.unlink(staging_path)
            else:
                shutil.rmtree(staging_path, ignore_errors=True)

        # Build the full hardlink tree before touching the original
        if not _create_hardlink_directory_tree(hdd_dir_path, staging_path):
            logger.error(f"Failed to build hardlink tree for {dir_path}; leaving original untouched")
            shutil.rmtree(staging_path, ignore_errors=True)
            return False

        # Replacement is ready — now remove the original and swap it in
        if is_symlink:
            # For symlinks, just unlink
            os.unlink(dir_path)
        else:
            # For regular directories, remove the entire tree
            shutil.rmtree(dir_path)

        os.rename(staging_path, dir_path)
        return True
        
    except Exception as e:
        logger.error(f"Failed to replace directory: {e}")
        shutil.rmtree(staging_path, ignore_errors=True)
        return False


def _replace_single_symlink_with_hardlink(symlink_path: str, hdd_target_path: str) -> bool:
    """
    Replace a single symlink with a hardlink
    
    Args:
        symlink_path: Path to the symlink to replace
        hdd_target_path: Path to the HDD file to hardlink to
    
    Returns:
        bool: True if successful
    """
    try:
        # Verify inputs
        if not os.path.islink(symlink_path):
            logger.error(f"Not a symlink: {symlink_path}")
            return False
        
        if not os.path.exists(hdd_target_path):
            logger.error(f"HDD target does not exist: {hdd_target_path}")
            return False
        
        # Check if it's a directory or file
        is_directory = os.path.isdir(hdd_target_path)
        
        if is_directory:
            # For directories, we need to handle this differently
            # Remove the symlink and create a directory structure with hardlinks
            return _replace_directory_symlink_with_hardlinks(symlink_path, hdd_target_path)
        else:
            # For files, create a simple hardlink
            # Create temporary hardlink first for atomic replacement
            temp_hardlink = f"{symlink_path}.tmp_hardlink"
            
            try:
                # Create hardlink to HDD file
                os.link(hdd_target_path, temp_hardlink)
                
                # Atomically replace symlink with hardlink
                os.replace(temp_hardlink, symlink_path)
                
                logger.debug(f"Created hardlink: {symlink_path} -> {hdd_target_path}")
                return True
                
            except OSError as e:
                # Clean up temp file if it exists
                if os.path.exists(temp_hardlink):
                    try:
                        os.unlink(temp_hardlink)
                    except:
                        pass
                logger.error(f"Failed to create hardlink: {e}")
                return False
    
    except Exception as e:
        logger.error(f"Unexpected error replacing symlink: {e}")
        return False

def _replace_directory_symlink_with_hardlinks(symlink_path: str, hdd_dir_path: str) -> bool:
    """
    Replace a directory symlink with a directory containing hardlinks to all files
    
    Args:
        symlink_path: Path to the directory symlink
        hdd_dir_path: Path to the HDD directory
    
    Returns:
        bool: True if successful
    """
    try:
        # Remove the symlink
        os.unlink(symlink_path)
        
        # Create the directory structure with hardlinks
        return _create_hardlink_directory_tree(hdd_dir_path, symlink_path)
        
    except Exception as e:
        logger.error(f"Failed to replace directory symlink: {e}")
        return False

