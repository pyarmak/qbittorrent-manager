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
        
        # Remove the symlink
        os.unlink(symlink_path)
        
        if is_multi_file:
            # For directories, we need to recreate the directory structure with hardlinks
            return _create_hardlink_directory_tree(hdd_source_path, symlink_path)
        else:
            # For single files, create a simple hardlink
            os.link(hdd_source_path, symlink_path)
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
        # Create target directory
        os.makedirs(target_dir, exist_ok=True)
        
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
                if os.path.exists(target_file):
                    os.unlink(target_file)
                
                # Create hardlink
                os.link(source_file, target_file)
        
        logger.info(f"✅ Replaced symlink directory with hardlink tree: {target_dir} -> {source_dir}")
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
    Replace symlinks with hardlinks to HDD files
    
    Args:
        symlink_paths: List of symlink paths to replace
        ssd_path: Original SSD path (for path translation)
        hdd_path: HDD path where files were copied
    
    Returns:
        int: Number of symlinks successfully replaced
    """
    replaced_count = 0
    
    if not symlink_paths:
        logger.debug("No symlinks to replace")
        return replaced_count
    
    # Normalize paths for comparison
    ssd_path_normalized = os.path.normpath(os.path.abspath(ssd_path))
    hdd_path_normalized = os.path.normpath(os.path.abspath(hdd_path))
    
    logger.info(f"Replacing {len(symlink_paths)} symlink(s) with hardlinks")
    logger.debug(f"SSD path: {ssd_path_normalized}")
    logger.debug(f"HDD path: {hdd_path_normalized}")
    
    for symlink_path in symlink_paths:
        try:
            if not os.path.islink(symlink_path):
                logger.warning(f"Path is not a symlink, skipping: {symlink_path}")
                continue
            
            # Get the current target of the symlink
            current_target = os.readlink(symlink_path)
            
            # Convert relative target to absolute
            if not os.path.isabs(current_target):
                current_target = os.path.join(os.path.dirname(symlink_path), current_target)
            
            current_target_normalized = os.path.normpath(os.path.abspath(current_target))
            
            # Calculate the corresponding HDD path
            # Replace the SSD base path with the HDD base path
            try:
                rel_path = os.path.relpath(current_target_normalized, ssd_path_normalized)
                if rel_path.startswith('..'):
                    logger.warning(f"Symlink target is not within SSD path, skipping: {symlink_path}")
                    continue
                
                # Construct the HDD target path
                if rel_path == '.':
                    hdd_target_path = hdd_path_normalized
                else:
                    hdd_target_path = os.path.join(hdd_path_normalized, rel_path)
                
            except ValueError:
                logger.warning(f"Cannot calculate relative path for symlink: {symlink_path}")
                continue
            
            # Verify the HDD target exists
            if not os.path.exists(hdd_target_path):
                logger.error(f"HDD target does not exist, skipping: {hdd_target_path}")
                continue
            
            # Replace symlink with hardlink
            if _replace_single_symlink_with_hardlink(symlink_path, hdd_target_path):
                replaced_count += 1
                logger.info(f"✅ Replaced symlink: {symlink_path}")
            else:
                logger.error(f"❌ Failed to replace symlink: {symlink_path}")
        
        except Exception as e:
            logger.error(f"Error processing symlink {symlink_path}: {e}")
            continue
    
    logger.info(f"Successfully replaced {replaced_count}/{len(symlink_paths)} symlinks with hardlinks")
    return replaced_count

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

