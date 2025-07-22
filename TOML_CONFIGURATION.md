# TOML Configuration Guide

The qBittorrent Manager now uses TOML configuration files instead of environment variables for better organization and easier management.

## Overview

### Benefits of TOML Configuration
- **Better Organization**: Structured sections for different functionality
- **Type Safety**: Native support for strings, numbers, booleans, and arrays
- **Comments**: Inline documentation and explanations
- **Validation**: Built-in validation with clear error messages
- **Version Control**: Human-readable format suitable for Git
- **Environment Overrides**: Environment variables can still override TOML values

## Configuration File Location

The service looks for configuration files in this order:

1. `/config/config.toml` (Container mount - recommended)
2. `config.toml` (Current directory)
3. `config.toml.example` (Development fallback)

## Configuration Structure

### [general]
Basic service settings:
```toml
[general]
timezone = "America/New_York"  # Timezone for logging
puid = 1000                    # User ID for file operations
pgid = 1000                    # Group ID for file operations  
log_level = "INFO"             # DEBUG, INFO, WARNING, ERROR
dry_run = false                # Enable dry-run mode
```

### [paths]
Storage path configuration:
```toml
[paths.downloads]
ssd = "/downloads/ssd"         # Fast storage for active downloads
hdd = "/downloads/hdd"         # Slow storage for completed torrents

[paths.config]
base = "/config"               # Base configuration directory
logs = "/config/log"           # Log file directory
state = "/config/qbit-manager-state"  # State persistence directory
```

### [qbittorrent]
qBittorrent connection settings:
```toml
[qbittorrent]
host = "localhost"             # qBittorrent host
port = 8080                    # qBittorrent WebUI port
username = "admin"             # WebUI username
password = "adminadmin"        # WebUI password
verify_ssl = true              # Verify SSL certificates
```

### [http_service]
HTTP API service configuration:
```toml
[http_service]
enabled = true                 # Enable HTTP service
host = "127.0.0.1"            # Bind address (127.0.0.1 for localhost only)
port = 8081                    # HTTP service port
api_key = "your-secure-key"    # API authentication key (REQUIRED)
```

### [processing]
Torrent processing settings:
```toml
[processing.concurrency]
max_concurrent = 3             # Maximum simultaneous torrents

[processing.storage] 
threshold_gb = 100             # SSD space threshold in GB

[processing.copy]
retry_attempts = 3             # Copy retry attempts
verification_enabled = true    # Enable copy verification
```

### [notifications]
Arr application notifications:
```toml
[notifications]
enabled = true                 # Enable Arr notifications

[notifications.sonarr]
url = "http://sonarr:8989"    # Sonarr URL
api_key = "your-sonarr-key"   # Sonarr API key
tag = "sonarr"                # Tag for Sonarr torrents

[notifications.radarr]
url = "http://radarr:7878"    # Radarr URL
api_key = "your-radarr-key"   # Radarr API key
tag = "radarr"                # Tag for Radarr torrents
```

### [storage_tags]
Storage location tagging:
```toml
[storage_tags]
enabled = true                 # Enable location tagging
auto_tag_new = true           # Auto-tag new torrents
ssd_tag = "ssd"               # SSD location tag
hdd_tag = "hdd"               # HDD location tag
```

### [logging]
Logging configuration:
```toml
[logging]
file = "/config/log/qbit-manager.log"  # Log file path
console = true                          # Console logging for Docker
detailed = false                        # Detailed debug logging
```

## Environment Variable Overrides

Environment variables can still override TOML configuration values:

| Environment Variable | TOML Path | Purpose |
|---------------------|-----------|---------|
| `PUID` | `general.puid` | User ID |
| `PGID` | `general.pgid` | Group ID |
| `HTTP_API_KEY` | `http_service.api_key` | API key |
| `QBIT_PASSWORD` | `qbittorrent.password` | qBittorrent password |
| `DOWNLOAD_PATH_SSD` | `paths.downloads.ssd` | SSD path |
| `FINAL_DEST_BASE_HDD` | `paths.downloads.hdd` | HDD path |

This allows for secure password management and Docker compatibility.

## Container Setup

### 1. Docker Compose (Recommended)

```yaml
version: '3.8'

services:
  qbit-manager:
    image: qbit-manager:latest
    container_name: qbit-manager
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    volumes:
      # Mount configuration directory
      - ./config/qbit-manager:/config
      # Mount downloads directory
      - ./downloads:/downloads
    ports:
      - "8081:8081"
    restart: unless-stopped
```

### 2. Directory Structure

```
./config/qbit-manager/
├── config.toml              # Main configuration file
├── log/                     # Log files
│   └── qbit-manager.log
└── qbit-manager-state/      # State persistence
    └── orchestrator_state.json
```

### 3. Initial Setup

1. **Start the container** - it will create a default `config.toml`
2. **Edit the configuration**:
   ```bash
   nano ./config/qbit-manager/config.toml
   ```
3. **Set required values**:
   - `http_service.api_key` - Set a secure random key
   - `qbittorrent.password` - Your qBittorrent password
   - `notifications.*.api_key` - Your Sonarr/Radarr API keys
4. **Restart the container** to apply changes

## Configuration Validation

The service performs comprehensive validation on startup:

### Required Settings
- `http_service.api_key` must be set (minimum 16 characters recommended)
- `qbittorrent.username` and `qbittorrent.password` must be configured
- Storage paths must exist and be writable

### Warnings
- Default API keys trigger security warnings
- Unusual threshold values generate warnings
- Missing optional API keys show feature warnings

### Error Handling
- Invalid TOML syntax shows clear error messages
- Missing required values prevent service startup
- Path validation ensures directory accessibility

## Migration from Environment Variables

### Automated Migration
1. **Stop the old container**
2. **Update to TOML-based image**
3. **Start container** - it creates `config.toml` with defaults
4. **Transfer your settings** from old environment variables to TOML sections
5. **Remove environment variables** from docker-compose.yml (except PUID/PGID)

### Manual Migration Script
```bash
#!/bin/bash
# migrate-env-to-toml.sh

# Read old environment file
source .env

# Create TOML configuration
cat > config.toml << EOF
[general]
log_level = "${LOG_LEVEL:-INFO}"
dry_run = ${DRY_RUN:-false}

[qbittorrent]
host = "${QBIT_HOST:-localhost}"
port = ${QBIT_PORT:-8080}
username = "${QBIT_USERNAME:-admin}"
password = "${QBIT_PASSWORD}"

[http_service]
enabled = true
host = "127.0.0.1"
port = ${HTTP_PORT:-8081}
api_key = "${HTTP_API_KEY}"

[paths.downloads]
ssd = "${DOWNLOAD_PATH_SSD:-/downloads/ssd}"
hdd = "${FINAL_DEST_BASE_HDD:-/downloads/hdd}"

[notifications.sonarr]
url = "${SONARR_URL:-http://sonarr:8989}"
api_key = "${SONARR_API_KEY}"

[notifications.radarr]
url = "${RADARR_URL:-http://radarr:7878}"
api_key = "${RADARR_API_KEY}"
EOF

echo "TOML configuration created. Review and edit config.toml"
```

## Security Considerations

### API Key Security
- Use a strong, random API key (32+ characters)
- Store sensitive keys in environment variables if needed
- Restrict HTTP service binding (`127.0.0.1` for localhost only)

### File Permissions
- Configuration files are owned by PUID:PGID
- Log directory is writable by the service user
- State directory requires read/write access

### Network Security
- Bind HTTP service to localhost by default
- Use Docker networks for container communication
- Consider reverse proxy for external access

## Troubleshooting

### Common Issues

1. **"Configuration file not found"**
   - Ensure `/config/config.toml` exists
   - Check volume mount in docker-compose.yml
   - Verify file permissions

2. **"TOML parsing error"**
   - Validate TOML syntax: https://www.toml-lint.com/
   - Check for missing quotes around strings
   - Ensure proper section headers

3. **"HTTP API key must be set"**
   - Set `http_service.api_key` in config.toml
   - Or set `HTTP_API_KEY` environment variable
   - Use a secure random string

4. **"qBittorrent connection failed"**
   - Verify qBittorrent credentials in config
   - Check network connectivity between containers
   - Ensure qBittorrent WebUI is enabled

### Debug Mode
Enable detailed logging:
```toml
[general]
log_level = "DEBUG"

[logging]
detailed = true
```

This provides verbose output for troubleshooting configuration and connection issues. 