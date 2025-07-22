# qBittorrent Manager HTTP Service API

The qBittorrent Manager now runs as a persistent HTTP service that orchestrates torrent processing, space management, and other tasks. This replaces the old file-based locking and queueing system with a centralized in-memory orchestrator.

## Configuration

### Environment Variables

```bash
# HTTP Service Configuration
HTTP_ENABLED=true              # Enable/disable HTTP service (default: true)
HTTP_HOST=127.0.0.1           # Host to bind to (default: 127.0.0.1 for security)
HTTP_PORT=8081                # Port to listen on (default: 8081)
HTTP_API_KEY=your-secret-key  # Required API key for authentication

# Other existing configuration...
DOWNLOAD_PATH_SSD=/downloads/ssd
FINAL_DEST_BASE_HDD=/downloads/hdd
# ... etc
```

**⚠️ IMPORTANT**: The `HTTP_API_KEY` must be set to a secure random string when the service is enabled.

### Starting the Service

The service starts automatically when the container runs, using LinuxServer.io's custom services:

```bash
# Manual start (for testing)
python3 /qbit-manager/main.py --service
```

## API Endpoints

All endpoints (except `/health`) require authentication via:
- Header: `X-API-Key: your-secret-key`
- Query parameter: `?api_key=your-secret-key`

### Health Check
```http
GET /health
```
Returns service health status. No authentication required.

### Service Status
```http
GET /status
```
Returns detailed service status including:
- Service uptime and last activity
- Currently running processes
- Queue status and capacity
- Processing statistics

### Torrent Completion Notification
```http
POST /notify/torrent-finished
Content-Type: application/json

{
  "hash": "torrent_hash_here",
  "params": {
    "hash": "torrent_hash_here",
    "name": "Torrent Name",
    "category": "movies",
    "content_path": "/downloads/ssd/movies/movie.mkv",
    "num_files": 1,
    "size": 1234567890,
    "tags": "tag1 tag2"
  },
  "priority": 0
}
```
Notifies the service that a torrent has completed. The `params` object enables optimized processing without API calls.

### Manual Space Management
```http
POST /space-management/trigger
```
Manually triggers space management process.

### Queue Management
```http
POST /queue/clear
```
Clears all items from the processing queue.

### Torrent Tagging
```http
POST /tags/existing
Content-Type: application/json

{
  "dry_run": false
}
```
Tags existing torrents based on their storage location.

```http
GET /tags/summary
```
Gets summary of current location tagging status.

### State Management
```http
POST /state/save
```
Manually save the current orchestrator state to disk. Useful for creating recovery points.

## qBittorrent Configuration

### Method 1: Notification Script (Recommended)

1. Copy the example script: `examples/qbittorrent-notification.sh`
2. Make it executable: `chmod +x qbittorrent-notification.sh`
3. In qBittorrent, go to **Settings > Downloads > Run external program on torrent completed**
4. Set the command to:
   ```bash
   /path/to/qbittorrent-notification.sh "%I" "%N" "%L" "%F" %C %Z "%G"
   ```

### Method 2: Direct cURL (Simple)

Set the external program to:
```bash
curl -X POST -H "X-API-Key: $HTTP_API_KEY" -H "Content-Type: application/json" -d '{"hash":"%I"}' http://localhost:8081/notify/torrent-finished
```

### Method 3: qBittorrent Plugin (Advanced)

For more advanced integration, you can create a qBittorrent plugin that:
1. Hooks into torrent completion events
2. Sends optimized notifications with full torrent parameters
3. Handles retries and error conditions

## Service Architecture Benefits

### Centralized Orchestration
- All torrent processing is managed by a single service
- Real-time visibility into running processes and queue status
- Intelligent load balancing and concurrency control

### Simplified Locking
- No more file-based locks and race conditions
- In-memory process tracking and synchronization
- Atomic queue operations

### Better Resource Management
- Configurable concurrency limits
- Automatic space management after each torrent
- Background processing with proper error handling

### Remote Management
- HTTP API allows remote control and monitoring
- Integration with external tools and dashboards
- Programmatic access to all functionality

### Graceful Shutdown & Recovery
- **State Persistence**: Queue and running processes are saved during shutdown
- **Automatic Recovery**: On restart, interrupted work is automatically restored
- **Zero Data Loss**: No torrents are lost during container restarts
- **Legacy Migration**: Automatically migrates old file-based queue items

## Monitoring and Troubleshooting

### Check Service Status
```bash
curl -H "X-API-Key: your-key" http://localhost:8081/status
```

### View Logs
```bash
# Container logs
docker logs your-container-name

# Service-specific logs
tail -f /config/log/qbit-manager.log
```

### Common Issues

1. **Service won't start**: Check that `HTTP_API_KEY` is set
2. **Authentication failures**: Verify API key in requests
3. **qBittorrent not notifying**: Check external program configuration
4. **Port conflicts**: Ensure port 8081 is not used by other services

## Service-Only Architecture

The qBittorrent Manager now operates exclusively as an HTTP service:
- **No CLI commands**: All functionality is accessed through HTTP API endpoints
- **No file-based queuing**: Uses in-memory orchestration with state persistence
- **No background processes**: Uses thread pool execution within the service
- **Simplified deployment**: Single service process handles everything

All interactions are through the HTTP API. Use the endpoints documented above instead of CLI commands. 