#!/usr/bin/env python3
"""
qBittorrent Manager Service - HTTP API Orchestrator

This module implements a persistent HTTP service that orchestrates torrent processing,
space management, and other tasks. It replaces the old file-based locking and queueing
system with a centralized in-memory orchestrator.
"""

import asyncio
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional
import uuid
from concurrent.futures import ThreadPoolExecutor
import signal
import sys

# HTTP server imports
try:
    from aiohttp import web
    from aiohttp.web import middleware
except ImportError:
    print("ERROR: aiohttp is required for the service. Install with: pip install aiohttp")
    sys.exit(1)

# Import our modules
import config
from classes import BTIH, TorrentInfo, ServiceStatus, ProcessInfo, QueueItem
from qbit import get_qbit_client
from tasks import process_torrent_unified
from core import manage_ssd_space
from tags import tag_existing_torrents_by_location, get_location_tag_summary

# Import logging
try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-service')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-service')

# ===================================================================
# Orchestrator Class
# ===================================================================

class QbitManagerOrchestrator:
    """
    Central orchestrator for managing torrent processing, space management,
    and other tasks via HTTP API.
    """
    
    def __init__(self):
        self.running_processes: Dict[str, ProcessInfo] = {}
        self.process_queue: List[QueueItem] = []
        # Add copy operation tracking
        self.running_copy_operations: Dict[str, Dict] = {}
        self.copy_queue: List[Dict] = []
        self.executor = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_PROCESSES)
        self.copy_executor = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_COPY_OPERATIONS if hasattr(config, 'MAX_CONCURRENT_COPY_OPERATIONS') else 2)
        self.qbit_client = None
        self.shutdown_event = threading.Event()
        self.lock = threading.RLock()  # Reentrant lock for thread safety
        self._shutdown_in_progress = False  # Flag to prevent new work during shutdown
        
        # Statistics
        self.stats = {
            'service_start_time': time.time(),
            'torrents_processed': 0,
            'space_management_runs': 0,
            'api_requests': 0,
            'last_activity': time.time(),
            'copy_operations_completed': 0,
            'copy_operations_failed': 0
        }
        
        logger.info("qBittorrent Manager Orchestrator initialized")
        
        # Try to restore previous state
        self._restore_previous_state()
    
    def get_qbit_client(self):
        """Get or create qBittorrent client instance"""
        if self.qbit_client is None:
            self.qbit_client = get_qbit_client()
        return self.qbit_client
    
    def add_to_queue(self, torrent_hash: str, torrent_params: Optional[Dict] = None, priority: int = 0) -> str:
        """Add a torrent to the processing queue"""
        with self.lock:
            # Don't accept new work if shutdown is in progress
            if self._shutdown_in_progress:
                logger.warning(f"Rejecting new torrent {torrent_hash} - shutdown in progress")
                raise RuntimeError("Service is shutting down")
            
            # Create TorrentInfo object from available data
            if torrent_params:
                # Use optimized creation from qBittorrent parameters
                torrent_info = TorrentInfo.from_qbittorrent_params(torrent_params)
            else:
                # Create minimal TorrentInfo from hash only
                torrent_info = TorrentInfo.from_hash_only(torrent_hash)
            
            queue_id = str(uuid.uuid4())
            item = QueueItem(
                id=queue_id,
                torrent=torrent_info,
                queued_time=time.time(),
                priority=priority
            )
            self.process_queue.append(item)
            # Sort by priority (higher first), then by time (older first)
            self.process_queue.sort(key=lambda x: (-x.priority, x.queued_time))
            
            logger.info(f"Added torrent {torrent_hash} to queue with ID {queue_id}")
            self._process_queue()
            return queue_id
    
    def _process_queue(self):
        """Process items from the queue if capacity is available"""
        with self.lock:
            while (len(self.running_processes) < config.MAX_CONCURRENT_PROCESSES 
                   and self.process_queue):
                
                item = self.process_queue.pop(0)
                self._start_torrent_processing(item)
            
            # Trigger space management after successful processing if queue is empty
            if not self.process_queue:
                self._trigger_space_management()
    
    def _start_torrent_processing(self, queue_item: QueueItem):
        """Start processing a torrent in a background thread"""
        process_id = str(uuid.uuid4())
        process_info = ProcessInfo(
            id=process_id,
            torrent_hash=str(queue_item.torrent.hash),  # Use hash from TorrentInfo
            start_time=time.time(),
            status=ServiceStatus.RUNNING
        )
        
        self.running_processes[process_id] = process_info
        
        logger.info(f"Starting torrent processing: {queue_item.torrent.hash} (Process ID: {process_id})")
        
        # Submit to thread pool
        future = self.executor.submit(
            self._process_torrent_worker, 
            queue_item
        )
        
        # Add callback to handle completion
        future.add_done_callback(lambda f: self._on_process_complete(process_id, f))
    
    def _process_torrent_worker(self, queue_item: QueueItem):
        """Worker function that runs torrent processing in a background thread"""
        try:
            client = self.get_qbit_client()
            
            # Use unified processing function - it handles both complete and incomplete data intelligently
            success = process_torrent_unified(client, queue_item.torrent)
            
            return {'success': success, 'torrent_hash': str(queue_item.torrent.hash)}
            
        except Exception as e:
            logger.error(f"Error processing torrent {queue_item.torrent.hash}: {e}")
            return {'success': False, 'error': str(e), 'torrent_hash': str(queue_item.torrent.hash)}
    
    def _on_process_complete(self, process_id: str, future):
        """Callback when a torrent processing task completes"""
        with self.lock:
            if process_id not in self.running_processes:
                return
            
            process_info = self.running_processes[process_id]
            
            try:
                result = future.result()
                process_info.status = ServiceStatus.COMPLETED if result['success'] else ServiceStatus.FAILED
                process_info.result = result
                
                if result['success']:
                    self.stats['torrents_processed'] += 1
                    logger.info(f"Torrent processing completed: {result['torrent_hash']}")
                else:
                    logger.error(f"Torrent processing failed: {result.get('torrent_hash', 'unknown')}")
                    
            except Exception as e:
                process_info.status = ServiceStatus.FAILED
                process_info.result = {'success': False, 'error': str(e)}
                logger.error(f"Exception in torrent processing: {e}")
            
            # Clean up old completed processes (keep last 10)
            completed_processes = [p for p in self.running_processes.values() 
                                 if p.status in [ServiceStatus.COMPLETED, ServiceStatus.FAILED]]
            if len(completed_processes) > 10:
                oldest_completed = sorted(completed_processes, key=lambda x: x.start_time)[:-10]
                for old_process in oldest_completed:
                    del self.running_processes[old_process.id]
            
            # Process next item in queue
            self._process_queue()
            self.stats['last_activity'] = time.time()
    
    def _trigger_space_management(self):
        """Trigger space management in a background thread"""
        def run_space_management():
            try:
                logger.info("Starting space management after torrent completion")
                client = self.get_qbit_client()
                manage_ssd_space(client)
                
                with self.lock:
                    self.stats['space_management_runs'] += 1
                    self.stats['last_activity'] = time.time()
                    
                logger.info("Space management completed")
            except Exception as e:
                logger.error(f"Error in space management: {e}")
        
        # Run in thread pool
        self.executor.submit(run_space_management)
    
    def add_copy_operations(self, copy_operations_list: List[Dict]) -> str:
        """Add copy operations to the queue for async processing"""
        with self.lock:
            if self._shutdown_in_progress:
                logger.warning("Rejecting copy operations - shutdown in progress")
                raise RuntimeError("Service is shutting down")
            
            batch_id = str(uuid.uuid4())
            
            for copy_op in copy_operations_list:
                copy_op['batch_id'] = batch_id
                copy_op['queued_time'] = time.time()
                copy_op['id'] = str(uuid.uuid4())
                self.copy_queue.append(copy_op)
            
            logger.info(f"Added {len(copy_operations_list)} copy operations with batch ID {batch_id}")
            self._process_copy_queue()
            return batch_id
    
    def _process_copy_queue(self):
        """Process copy operations from the queue if capacity is available"""
        with self.lock:
            max_concurrent = getattr(config, 'MAX_CONCURRENT_COPY_OPERATIONS', 2)
            while (len(self.running_copy_operations) < max_concurrent 
                   and self.copy_queue):
                
                copy_item = self.copy_queue.pop(0)
                self._start_copy_operation(copy_item)
    
    def _start_copy_operation(self, copy_item: Dict):
        """Start a copy operation in a background thread"""
        copy_id = copy_item['id']
        
        # Track the running operation
        self.running_copy_operations[copy_id] = {
            'id': copy_id,
            'batch_id': copy_item['batch_id'],
            'torrent_hash': copy_item['hash'],
            'torrent_name': copy_item['name'],
            'start_time': time.time(),
            'status': ServiceStatus.RUNNING,
            'ssd_path': copy_item['ssd_path'],
            'hdd_path': copy_item['hdd_path'],
            'size': copy_item.get('size', 0)
        }
        
        logger.info(f"Starting copy operation: {copy_item['name']} (Copy ID: {copy_id})")
        
        # Submit to copy thread pool
        future = self.copy_executor.submit(self._copy_worker, copy_item)
        future.add_done_callback(lambda f: self._on_copy_complete(copy_id, f))
    
    def _copy_worker(self, copy_item: Dict):
        """Worker function that runs copy operations in a background thread"""
        import shutil
        import os
        from util import verify_copy
        
        try:
            logger.info(f"📁 Copying {copy_item['name']} from SSD to HDD...")
            
            # Use is_multi_file from data
            is_multi_file = copy_item.get('is_multi_file', os.path.isdir(copy_item['ssd_path']))
            
            # Ensure HDD base directory exists
            hdd_base_dir = os.path.dirname(copy_item['hdd_path'])
            os.makedirs(hdd_base_dir, exist_ok=True)
            
            # Perform copy operation
            copy_start_time = time.time()
            
            if is_multi_file:
                shutil.copytree(copy_item['ssd_path'], copy_item['hdd_path'], 
                              copy_function=shutil.copy2, dirs_exist_ok=True)
            else:
                shutil.copy2(copy_item['ssd_path'], copy_item['hdd_path'])
            
            copy_time = time.time() - copy_start_time
            logger.info(f"   ✅ Copy completed in {copy_time:.1f}s")
            
            # Verify copy
            if verify_copy(copy_item['ssd_path'], copy_item['hdd_path'], is_multi_file):
                logger.info(f"   ✅ Copy verification successful")
                
                # Add HDD tag after successful copy
                client = self.get_qbit_client()
                import config
                client.torrents_add_tags(tags=config.HDD_LOCATION_TAG, torrent_hashes=copy_item['hash'])
                logger.info(f"   🏷️  Added '{config.HDD_LOCATION_TAG}' tag to {copy_item['name']}")
                
                return {
                    'success': True,
                    'copy_time': copy_time,
                    'torrent_hash': copy_item['hash'],
                    'torrent_name': copy_item['name']
                }
            else:
                logger.error(f"   ❌ Copy verification failed for {copy_item['name']}")
                # Clean up failed copy
                try:
                    if os.path.exists(copy_item['hdd_path']):
                        if os.path.isdir(copy_item['hdd_path']):
                            shutil.rmtree(copy_item['hdd_path'])
                        else:
                            os.remove(copy_item['hdd_path'])
                except Exception as cleanup_e:
                    logger.warning(f"Failed to cleanup failed copy: {cleanup_e}")
                
                return {
                    'success': False,
                    'error': 'Copy verification failed',
                    'torrent_hash': copy_item['hash'],
                    'torrent_name': copy_item['name']
                }
                
        except Exception as e:
            logger.error(f"   ❌ Copy failed for {copy_item['name']}: {e}")
            return {
                'success': False,
                'error': str(e),
                'torrent_hash': copy_item['hash'],
                'torrent_name': copy_item['name']
            }
    
    def _on_copy_complete(self, copy_id: str, future):
        """Callback when a copy operation completes"""
        with self.lock:
            if copy_id not in self.running_copy_operations:
                return
            
            copy_info = self.running_copy_operations[copy_id]
            
            try:
                result = future.result()
                copy_info['status'] = ServiceStatus.COMPLETED if result['success'] else ServiceStatus.FAILED
                copy_info['result'] = result
                copy_info['end_time'] = time.time()
                copy_info['duration'] = copy_info['end_time'] - copy_info['start_time']
                
                if result['success']:
                    self.stats['copy_operations_completed'] += 1
                    logger.info(f"Copy operation completed: {result['torrent_name']}")
                else:
                    self.stats['copy_operations_failed'] += 1
                    logger.error(f"Copy operation failed: {result.get('torrent_name', 'unknown')}")
                    
            except Exception as e:
                copy_info['status'] = ServiceStatus.FAILED
                copy_info['result'] = {'success': False, 'error': str(e)}
                copy_info['end_time'] = time.time()
                copy_info['duration'] = copy_info['end_time'] - copy_info['start_time']
                self.stats['copy_operations_failed'] += 1
                logger.error(f"Exception in copy operation: {e}")
            
            # Clean up old completed copy operations (keep last 20)
            completed_copies = [p for p in self.running_copy_operations.values() 
                              if p['status'] in [ServiceStatus.COMPLETED, ServiceStatus.FAILED]]
            if len(completed_copies) > 20:
                oldest_completed = sorted(completed_copies, key=lambda x: x['start_time'])[:-20]
                for old_copy in oldest_completed:
                    del self.running_copy_operations[old_copy['id']]
            
            # Process next copy in queue
            self._process_copy_queue()
            self.stats['last_activity'] = time.time()
    
    def get_copy_operations_status(self, batch_id: str = None) -> Dict:
        """Get status of copy operations, optionally filtered by batch ID"""
        with self.lock:
            if batch_id:
                # Filter by batch ID
                operations = [op for op in self.running_copy_operations.values() 
                            if op.get('batch_id') == batch_id]
                queue_items = [op for op in self.copy_queue if op.get('batch_id') == batch_id]
            else:
                # All operations
                operations = list(self.running_copy_operations.values())
                queue_items = self.copy_queue.copy()
            
            return {
                'batch_id': batch_id,
                'running_operations': len([op for op in operations if op['status'] == ServiceStatus.RUNNING]),
                'completed_operations': len([op for op in operations if op['status'] == ServiceStatus.COMPLETED]),
                'failed_operations': len([op for op in operations if op['status'] == ServiceStatus.FAILED]),
                'queued_operations': len(queue_items),
                'operations': [
                    {
                        'id': op['id'],
                        'batch_id': op.get('batch_id'),
                        'torrent_hash': op['torrent_hash'],
                        'torrent_name': op['torrent_name'],
                        'status': op['status'].value,
                        'start_time': datetime.fromtimestamp(op['start_time']).isoformat(),
                        'duration_seconds': op.get('duration', time.time() - op['start_time']),
                        'size_gb': f"{op.get('size', 0) / (1024**3):.2f}" if op.get('size') else "Unknown"
                    }
                    for op in operations
                ]
            }
    
    def get_status(self) -> Dict:
        """Get current orchestrator status"""
        with self.lock:
            uptime = time.time() - self.stats['service_start_time']
            max_copy_concurrent = getattr(config, 'MAX_CONCURRENT_COPY_OPERATIONS', 2)
            
            return {
                'service': {
                    'status': ServiceStatus.RUNNING.value,
                    'uptime_seconds': uptime,
                    'uptime_human': f"{uptime/3600:.1f} hours",
                    'last_activity': datetime.fromtimestamp(self.stats['last_activity']).isoformat()
                },
                'processing': {
                    'running_processes': len(self.running_processes),
                    'max_concurrent': config.MAX_CONCURRENT_PROCESSES,
                    'queue_size': len(self.process_queue),
                    'capacity_available': config.MAX_CONCURRENT_PROCESSES - len(self.running_processes)
                },
                'copy_operations': {
                    'running_copies': len(self.running_copy_operations),
                    'max_concurrent_copies': max_copy_concurrent,
                    'copy_queue_size': len(self.copy_queue),
                    'copy_capacity_available': max_copy_concurrent - len(self.running_copy_operations)
                },
                'statistics': self.stats.copy(),
                'processes': [
                    {
                        'id': p.id,
                        'torrent_hash': p.torrent_hash,
                        'status': p.status.value,
                        'start_time': datetime.fromtimestamp(p.start_time).isoformat(),
                        'duration_seconds': time.time() - p.start_time
                    }
                    for p in self.running_processes.values()
                ]
            }
    
    def clear_queue(self) -> Dict:
        """Clear all items from the processing queue"""
        with self.lock:
            cleared_count = len(self.process_queue)
            self.process_queue.clear()
            logger.info(f"Cleared {cleared_count} items from processing queue")
            return {'cleared_count': cleared_count}
    
    def shutdown(self, save_state=True):
        """Gracefully shutdown the orchestrator"""
        logger.info("Initiating graceful orchestrator shutdown...")
        
        with self.lock:
            self._shutdown_in_progress = True
        
        self.shutdown_event.set()
        
        # Save current state before shutting down
        if save_state:
            logger.info("Saving orchestrator state for recovery...")
            self._save_current_state()
        
        # Wait for running processes to complete (with timeout)
        total_operations = len(self.running_processes) + len(self.running_copy_operations)
        if total_operations > 0:
            logger.info(f"Waiting for {len(self.running_processes)} torrent processes and {len(self.running_copy_operations)} copy operations to complete...")
            logger.info("Operations will be restored on next startup if interrupted")
            
            # Give operations time to complete, but don't wait indefinitely
            start_time = time.time()
            timeout = 30  # 30 seconds
            
            while (self.running_processes or self.running_copy_operations) and (time.time() - start_time) < timeout:
                time.sleep(1)
                # Check if any operations completed
                completed_processes = [p for p in self.running_processes.values() if p.status != ServiceStatus.RUNNING]
                completed_copies = [p for p in self.running_copy_operations.values() if p['status'] != ServiceStatus.RUNNING]
                if completed_processes or completed_copies:
                    logger.info(f"{len(completed_processes)} processes and {len(completed_copies)} copy operations completed during shutdown")
            
            # Force shutdown executors
            self.executor.shutdown(wait=False, timeout=5)
            self.copy_executor.shutdown(wait=False, timeout=5)
            
            if self.running_processes or self.running_copy_operations:
                logger.warning(f"{len(self.running_processes)} processes and {len(self.running_copy_operations)} copy operations were interrupted and will be restored on restart")
        
        # Close qBittorrent client
        if self.qbit_client:
            try:
                from qbit import close_qbit_client
                close_qbit_client()
                logger.info("Closed qBittorrent client connection")
            except Exception as e:
                logger.warning(f"Error closing qBittorrent client: {e}")
        
        logger.info("Orchestrator shutdown complete")
    
    def _save_current_state(self):
        """Save current orchestrator state to disk"""
        try:
            from persistence import save_orchestrator_state
            success = save_orchestrator_state(self)
            if success:
                logger.info("Successfully saved orchestrator state")
            else:
                logger.error("Failed to save orchestrator state")
        except ImportError:
            logger.warning("Persistence module not available - state will not be saved")
        except Exception as e:
            logger.error(f"Error saving orchestrator state: {e}")
    
    def _restore_previous_state(self):
        """Restore orchestrator state from previous shutdown"""
        try:
            from persistence import load_orchestrator_state, restore_orchestrator_state, cleanup_state_file
            
            # Load previous state
            state = load_orchestrator_state()
            if state:
                logger.info("Found previous orchestrator state, restoring...")
                restore_orchestrator_state(self, state)
                
                # Start processing restored queue
                self._process_queue()
                
                # Clean up state file after successful restoration
                cleanup_state_file()
                
                logger.info("Successfully restored orchestrator state")
            else:
                logger.info("No previous state to restore")
                
        except ImportError:
            logger.debug("Persistence module not available - no state restoration")
        except Exception as e:
            logger.error(f"Error restoring orchestrator state: {e}")
            logger.info("Continuing with fresh state")

# ===================================================================
# HTTP API Server
# ===================================================================

# Global orchestrator instance
orchestrator = QbitManagerOrchestrator()

@middleware
async def auth_middleware(request, handler):
    """Middleware to validate API key"""
    if request.path == '/health':  # Health check doesn't require auth
        return await handler(request)
    
    api_key = request.headers.get('X-API-Key') or request.query.get('api_key')
    if api_key != config.HTTP_API_KEY:
        return web.json_response(
            {'error': 'Invalid or missing API key'}, 
            status=401
        )
    
    orchestrator.stats['api_requests'] += 1
    return await handler(request)

async def health_check(request):
    """Health check endpoint"""
    return web.json_response({'status': 'healthy', 'service': 'qbit-manager'})

async def status(request):
    """Get orchestrator status"""
    try:
        status_data = orchestrator.get_status()
        return web.json_response(status_data)
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def notify_torrent_finished(request):
    """Handle torrent completion notification from qBittorrent"""
    try:
        data = await request.json()
        logger.debug(f"Received torrent completion notification: {data}")
        
        # Extract torrent information
        torrent_hash = data.get('hash')
        if not torrent_hash:
            return web.json_response({'error': 'Missing torrent hash'}, status=400)
        
        # Validate hash format
        try:
            BTIH(torrent_hash)
        except (ValueError, TypeError) as e:
            return web.json_response({'error': f'Invalid torrent hash: {e}'}, status=400)
        
        # Get torrent parameters if provided (for optimized processing)
        torrent_params = data.get('params')
        priority = data.get('priority', 0)
        
        # Add to processing queue
        queue_id = orchestrator.add_to_queue(torrent_hash, torrent_params, priority)
        
        logger.info(f"Received torrent completion notification: {torrent_hash}")
        
        return web.json_response({
            'success': True,
            'queue_id': queue_id,
            'message': f'Torrent {torrent_hash} queued for processing'
        })
        
    except Exception as e:
        logger.error(f"Error handling torrent notification: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def trigger_space_management(request):
    """Manually trigger space management"""
    try:
        orchestrator._trigger_space_management()
        return web.json_response({
            'success': True,
            'message': 'Space management triggered'
        })
    except Exception as e:
        logger.error(f"Error triggering space management: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def clear_queue_endpoint(request):
    """Clear the processing queue"""
    try:
        result = orchestrator.clear_queue()
        return web.json_response({
            'success': True,
            'cleared_count': result['cleared_count']
        })
    except Exception as e:
        logger.error(f"Error clearing queue: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def tag_existing_endpoint(request):
    """Tag existing torrents by location"""
    try:
        data = await request.json() if request.can_read_body else {}
        dry_run = data.get('dry_run', False)
        
        client = orchestrator.get_qbit_client()
        # Use async_copies=True to prevent blocking on copy operations
        result = tag_existing_torrents_by_location(client, dry_run=dry_run, async_copies=True)
        
        if 'error' in result:
            return web.json_response({'error': result['error']}, status=500)
        
        # Handle copy operations asynchronously if not dry run
        copy_batch_id = None
        if not dry_run and 'copy_operations_list' in result and result['copy_operations_list']:
            try:
                copy_batch_id = orchestrator.add_copy_operations(result['copy_operations_list'])
                logger.info(f"Queued {len(result['copy_operations_list'])} copy operations with batch ID: {copy_batch_id}")
            except Exception as copy_e:
                logger.error(f"Failed to queue copy operations: {copy_e}")
                # Don't fail the entire request if copy queueing fails
                copy_batch_id = None
        
        response_data = {
            'success': True,
            'result': result
        }
        
        # Add copy batch ID if copy operations were queued
        if copy_batch_id:
            response_data['copy_batch_id'] = copy_batch_id
            response_data['message'] = f"Tagging completed. Copy operations queued with batch ID: {copy_batch_id}"
        
        return web.json_response(response_data)
        
    except Exception as e:
        logger.error(f"Error in tag existing endpoint: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def tag_summary_endpoint(request):
    """Get location tag summary"""
    try:
        client = orchestrator.get_qbit_client()
        result = get_location_tag_summary(client)
        
        if 'error' in result:
            return web.json_response({'error': result['error']}, status=500)
        
        return web.json_response({
            'success': True,
            'summary': result
        })
        
    except Exception as e:
        logger.error(f"Error in tag summary endpoint: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def save_state_endpoint(request):
    """Manually save orchestrator state"""
    try:
        orchestrator._save_current_state()
        return web.json_response({
            'success': True,
            'message': 'State saved successfully'
        })
    except Exception as e:
        logger.error(f"Error saving state: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def copy_operations_status_endpoint(request):
    """Get status of copy operations"""
    try:
        batch_id = request.query.get('batch_id')
        result = orchestrator.get_copy_operations_status(batch_id)
        
        return web.json_response({
            'success': True,
            'copy_status': result
        })
        
    except Exception as e:
        logger.error(f"Error getting copy operations status: {e}")
        return web.json_response({'error': str(e)}, status=500)

def create_app():
    """Create and configure the web application"""
    app = web.Application(middlewares=[auth_middleware])
    
    # Add routes
    app.router.add_get('/health', health_check)
    app.router.add_get('/status', status)
    app.router.add_post('/notify/torrent-finished', notify_torrent_finished)
    app.router.add_post('/space-management/trigger', trigger_space_management)
    app.router.add_post('/queue/clear', clear_queue_endpoint)
    app.router.add_post('/tags/existing', tag_existing_endpoint)
    app.router.add_get('/tags/summary', tag_summary_endpoint)
    app.router.add_post('/state/save', save_state_endpoint)
    app.router.add_get('/copy-operations/status', copy_operations_status_endpoint)
    
    return app

async def start_service():
    """Start the HTTP service"""
    if not config.HTTP_ENABLED:
        logger.error("HTTP service is disabled in configuration")
        return
    
    app = create_app()
    
    logger.info(f"Starting qBittorrent Manager Service on {config.HTTP_HOST}:{config.HTTP_PORT}")
    logger.info("API Endpoints:")
    logger.info("  GET  /health - Health check (no auth required)")
    logger.info("  GET  /status - Service status")
    logger.info("  POST /notify/torrent-finished - Torrent completion notification")
    logger.info("  POST /space-management/trigger - Trigger space management")
    logger.info("  POST /queue/clear - Clear processing queue")
    logger.info("  POST /tags/existing - Tag existing torrents")
    logger.info("  GET  /tags/summary - Get tag summary")
    logger.info("  POST /state/save - Manually save state")
    logger.info("  GET  /copy-operations/status - Get copy operations status")
    logger.info(f"Authentication: X-API-Key header or api_key query parameter required")
    
    # Set up graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        try:
            orchestrator.shutdown(save_state=True)
            logger.info("Graceful shutdown completed")
        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")
            logger.info("Forcing shutdown")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start the web server
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, config.HTTP_HOST, config.HTTP_PORT)
    await site.start()
    
    logger.info("Service started successfully")
    
    # Keep the service running
    try:
        while not orchestrator.shutdown_event.is_set():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
    finally:
        orchestrator.shutdown()
        await runner.cleanup()

def run_service():
    """Run the service (entry point)"""
    try:
        asyncio.run(start_service())
    except KeyboardInterrupt:
        logger.info("Service interrupted by user")
    except Exception as e:
        logger.error(f"Service error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    run_service() 