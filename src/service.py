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
        self.executor = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_PROCESSES)
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
            'last_activity': time.time()
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
    
    def get_status(self) -> Dict:
        """Get current orchestrator status"""
        with self.lock:
            uptime = time.time() - self.stats['service_start_time']
            
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
        if self.running_processes:
            logger.info(f"Waiting for {len(self.running_processes)} processes to complete...")
            logger.info("Processes will be restored on next startup if interrupted")
            
            # Give processes time to complete, but don't wait indefinitely
            start_time = time.time()
            timeout = 30  # 30 seconds
            
            while self.running_processes and (time.time() - start_time) < timeout:
                time.sleep(1)
                # Check if any processes completed
                completed = [p for p in self.running_processes.values() if p.status != ServiceStatus.RUNNING]
                if completed:
                    logger.info(f"{len(completed)} processes completed during shutdown")
            
            # Force shutdown executor
            self.executor.shutdown(wait=False, timeout=5)
            
            if self.running_processes:
                logger.warning(f"{len(self.running_processes)} processes were interrupted and will be restored on restart")
        
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
        result = tag_existing_torrents_by_location(client, dry_run=dry_run)
        
        if 'error' in result:
            return web.json_response({'error': result['error']}, status=500)
        
        return web.json_response({
            'success': True,
            'result': result
        })
        
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