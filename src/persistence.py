#!/usr/bin/env python3
"""
State Persistence Module for qBittorrent Manager

This module handles saving and restoring orchestrator state during graceful shutdowns
to ensure no work is lost when the container is restarted.
"""

import json
import os
import time
from typing import Dict, List, Optional, Any
from dataclasses import asdict, dataclass
from pathlib import Path

# Import logging
try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-persistence')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-persistence')

# ===================================================================
# State Data Structures
# ===================================================================

@dataclass
class PersistedQueueItem:
    """Serializable version of QueueItem for persistence"""
    id: str
    torrent_data: Dict[str, Any]  # Serialized TorrentInfo
    queued_time: float
    priority: int = 0

@dataclass
class PersistedProcessInfo:
    """Serializable version of ProcessInfo for persistence"""
    id: str
    torrent_hash: str
    start_time: float
    status: str
    result: Optional[Dict] = None

@dataclass
class ServiceState:
    """Complete service state for persistence"""
    queue_items: List[PersistedQueueItem]
    running_processes: List[PersistedProcessInfo]
    statistics: Dict[str, Any]
    shutdown_time: float
    version: str = "1.0"

# ===================================================================
# Persistence Functions
# ===================================================================

def get_state_file_path():
    """Get the path for the state persistence file"""
    import config
    state_dir = os.path.join(config.LOCK_DIR, 'state')
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, 'orchestrator_state.json')

def save_orchestrator_state(orchestrator):
    """
    Save the current orchestrator state to disk for graceful shutdown recovery.
    
    Args:
        orchestrator: QbitManagerOrchestrator instance
    """
    try:
        with orchestrator.lock:
            # Convert queue items to serializable format
            queue_items = []
            for item in orchestrator.process_queue:
                persisted_item = PersistedQueueItem(
                    id=item.id,
                    torrent_data=item.torrent.to_dict(),  # Serialize TorrentInfo
                    queued_time=item.queued_time,
                    priority=item.priority
                )
                queue_items.append(persisted_item)
            
            # Convert running processes to serializable format
            # Only persist processes that are still running (not completed/failed)
            running_processes = []
            for process in orchestrator.running_processes.values():
                if process.status.value == 'running':  # Use .value for enum
                    persisted_process = PersistedProcessInfo(
                        id=process.id,
                        torrent_hash=process.torrent_hash,
                        start_time=process.start_time,
                        status=process.status.value,  # Serialize enum value
                        result=process.result
                    )
                    running_processes.append(persisted_process)
            
            # Create complete state
            state = ServiceState(
                queue_items=queue_items,
                running_processes=running_processes,
                statistics=orchestrator.stats.copy(),
                shutdown_time=time.time()
            )
            
            # Save to file
            state_file = get_state_file_path()
            temp_file = f"{state_file}.tmp"
            
            with open(temp_file, 'w') as f:
                # Convert dataclasses to dict for JSON serialization
                state_dict = {
                    'queue_items': [asdict(item) for item in state.queue_items],
                    'running_processes': [asdict(proc) for proc in state.running_processes],
                    'statistics': state.statistics,
                    'shutdown_time': state.shutdown_time,
                    'version': state.version
                }
                json.dump(state_dict, f, indent=2)
            
            # Atomic move to avoid corruption
            os.replace(temp_file, state_file)
            
            logger.info(f"Saved orchestrator state: {len(queue_items)} queued, {len(running_processes)} running")
            return True
            
    except Exception as e:
        logger.error(f"Failed to save orchestrator state: {e}")
        return False

def load_orchestrator_state():
    """
    Load previously saved orchestrator state from disk.
    
    Returns:
        ServiceState or None if no valid state found
    """
    state_file = get_state_file_path()
    
    if not os.path.exists(state_file):
        logger.debug("No previous state file found")
        return None
    
    try:
        with open(state_file, 'r') as f:
            state_dict = json.load(f)
        
        # Validate version compatibility
        version = state_dict.get('version', '1.0')
        if version != '1.0':
            logger.warning(f"State file version {version} not supported, ignoring")
            return None
        
        # Check if state is too old (older than 24 hours)
        shutdown_time = state_dict.get('shutdown_time', 0)
        age_hours = (time.time() - shutdown_time) / 3600
        if age_hours > 24:
            logger.warning(f"State file is {age_hours:.1f} hours old, ignoring")
            return None
        
        # Reconstruct queue items
        queue_items = []
        for item_dict in state_dict.get('queue_items', []):
            queue_item = PersistedQueueItem(**item_dict)
            queue_items.append(queue_item)
        
        # Reconstruct running processes (these will be re-queued)
        running_processes = []
        for proc_dict in state_dict.get('running_processes', []):
            process_info = PersistedProcessInfo(**proc_dict)
            running_processes.append(process_info)
        
        # Create state object
        state = ServiceState(
            queue_items=queue_items,
            running_processes=running_processes,
            statistics=state_dict.get('statistics', {}),
            shutdown_time=shutdown_time
        )
        
        logger.info(f"Loaded orchestrator state: {len(queue_items)} queued, {len(running_processes)} interrupted")
        return state
        
    except Exception as e:
        logger.error(f"Failed to load orchestrator state: {e}")
        return None

def restore_orchestrator_state(orchestrator, state):
    """
    Restore orchestrator state from a previously saved state.
    
    Args:
        orchestrator: QbitManagerOrchestrator instance
        state: ServiceState object from load_orchestrator_state()
    """
    if not state:
        return
    
    try:
        with orchestrator.lock:
            # Restore queue items
            from classes import QueueItem, TorrentInfo  # Import here to avoid circular imports
            
            for persisted_item in state.queue_items:
                # Deserialize TorrentInfo from persisted data
                torrent_info = TorrentInfo.from_dict(persisted_item.torrent_data)
                
                queue_item = QueueItem(
                    id=persisted_item.id,
                    torrent=torrent_info,
                    queued_time=persisted_item.queued_time,
                    priority=persisted_item.priority
                )
                orchestrator.process_queue.append(queue_item)
            
            # Re-queue interrupted running processes
            # These were running when the service shut down, so we need to restart them
            for persisted_process in state.running_processes:
                logger.info(f"Re-queueing interrupted process: {persisted_process.torrent_hash}")
                
                # Create minimal TorrentInfo from hash for interrupted processes
                torrent_info = TorrentInfo.from_hash_only(persisted_process.torrent_hash)
                
                queue_item = QueueItem(
                    id=f"restored-{persisted_process.id}",
                    torrent=torrent_info,
                    queued_time=time.time(),  # Current time for restored items
                    priority=10  # Higher priority for interrupted processes
                )
                orchestrator.process_queue.append(queue_item)
            
            # Sort queue by priority and time
            orchestrator.process_queue.sort(key=lambda x: (-x.priority, x.queued_time))
            
            # Restore relevant statistics (but update timestamps)
            if state.statistics:
                orchestrator.stats['torrents_processed'] = state.statistics.get('torrents_processed', 0)
                orchestrator.stats['space_management_runs'] = state.statistics.get('space_management_runs', 0)
                # Note: Don't restore time-based stats as they would be invalid
            
            total_restored = len(state.queue_items) + len(state.running_processes)
            logger.info(f"Restored orchestrator state: {total_restored} items back in queue")
            
    except Exception as e:
        logger.error(f"Failed to restore orchestrator state: {e}")

def cleanup_state_file():
    """Remove the state file after successful restoration"""
    try:
        state_file = get_state_file_path()
        if os.path.exists(state_file):
            os.remove(state_file)
            logger.debug("Cleaned up state file after successful restoration")
    except Exception as e:
        logger.warning(f"Failed to cleanup state file: {e}")
