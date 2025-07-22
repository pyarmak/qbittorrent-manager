# qbit-manager Setup Instructions

## Quick Start with LinuxServer Mods

### 1. **Prerequisites**
- Docker and Docker Compose installed
- Copy `env.example` to `.env` and configure your paths

### 2. **No Build Required!**
The setup now uses LinuxServer's universal-package-install mod, so **no custom Docker build is needed**.

### 3. **Start the Stack**
```bash
# Simply start with compose - packages install automatically
docker compose up -d qbittorrent

# Monitor installation progress
docker compose logs qbittorrent -f
```

### 4. **Configure qBittorrent for Optimal Performance**
After starting qBittorrent, configure it for optimized processing:

1. **Access qBittorrent WebUI** (usually http://localhost:8080)
2. **Go to Tools → Options → Downloads**  
3. **Enable "Run external program on torrent finished"**
4. **Enter this command** (copy exactly):
   ```bash
   python3 /app/qbit-manager/main.py --qbit-hash-v1 "%I" --qbit-name "%N" --qbit-category "%L" --qbit-content-path "%F" --qbit-num-files %C --qbit-size %Z --qbit-tags "%G"
   ```

**⚠️ Important**: 
- Use quotes around string parameters: `"%I"`, `"%N"`, `"%L"`, `"%F"`, `"%G"`
- Don't quote numeric parameters: `%C`, `%Z`

### 5. **Verify Installation**
```bash
# Check that Python packages are installed
docker compose exec qbittorrent python3 -c "import qbittorrentapi; print('qBittorrent API ready!')"

# Check scripts are mounted
docker compose exec qbittorrent ls -la /app/qbit-manager/

# Test qbit-manager functionality
docker compose exec qbittorrent python3 /app/qbit-manager/main.py --status

# Test optimized mode (manual)
docker compose exec qbittorrent python3 /app/qbit-manager/main.py \
  --qbit-hash-v1 "ABC123" --qbit-name "Test" --qbit-category "sonarr" \
  --qbit-content-path "/downloads/ssd/test" --qbit-num-files 1 \
  --qbit-size 1073741824 --dry-run
```

## Configuration

### **Environment Variables**
All configuration is done via environment variables in your `.env` file:

```bash
# === qBittorrent Configuration ===
QBIT_USERNAME=admin
QBIT_PASSWORD=adminadmin
QBIT_WEB_PORT=8080

# === Storage Paths ===
DOWNLOAD_PATH_SSD=/downloads/ssd
FINAL_DEST_BASE_HDD=/downloads/hdd
DISK_SPACE_THRESHOLD_GB=100

# === Location Tagging ===
SSD_LOCATION_TAG=ssd
HDD_LOCATION_TAG=hdd
ENABLE_LOCATION_TAGGING=true
AUTO_TAG_NEW_TORRENTS=true

# === Arr Integration ===
SONARR_API_KEY=your_sonarr_api_key
RADARR_API_KEY=your_radarr_api_key
NOTIFY_ARR_ENABLED=true
```

### **Volume Mounts**
- **Scripts**: `./qbit-manager/pyrosimple-manager:/app/qbit-manager:ro` (read-only)
- **Config**: `${DOCKER_CONFIG_PATH}/qbittorrent:/config`
- **Downloads**: `${DOWNLOADS_PATH}:/downloads`

## Features

### **1. Automatic SSD Management**
- Monitors SSD space and relocates completed torrents to HDD
- Configurable space threshold
- Automatic cleanup and verification

### **2. Location Tagging System**
- Tags torrents with `ssd` or `hdd` based on storage location
- Efficient filtering for space management
- Automatic tag updates during relocation

### **3. Arr Integration**
- Notifies Sonarr/Radarr when torrents are moved to HDD
- Category-based service detection
- Automatic library scans

### **4. Background Processing**
- Non-blocking torrent processing
- Queue system for high-load scenarios
- Process limit management

## Usage Examples

### **🚀 Optimized Mode (Recommended)**
When configured in qBittorrent, this runs automatically with **90% fewer API calls**:
```bash
# This runs automatically when a torrent finishes in qBittorrent
# (No manual intervention needed)

# Manual testing of optimized mode
docker compose exec qbittorrent python3 /app/qbit-manager/main.py \
  --qbit-hash-v1 "TORRENT_HASH" \
  --qbit-name "Movie Title (2024)" \
  --qbit-category "radarr" \
  --qbit-content-path "/downloads/ssd/radarr/Movie Title (2024)" \
  --qbit-num-files 1 \
  --qbit-size 5368709120 \
  --qbit-tags "ssd"
```

### **📋 Standard Mode (Backup/Manual)**
For manual processing or when optimized mode isn't configured:
```bash
# Process specific torrent (makes API calls)
docker compose exec qbittorrent python3 /app/qbit-manager/main.py TORRENT_HASH

# Check background processes
docker compose exec qbittorrent python3 /app/qbit-manager/main.py --status

# Process queued torrents
docker compose exec qbittorrent python3 /app/qbit-manager/main.py --process-queue
```

### **🏷️ Tag Management**
```bash
# See current tagging status
docker compose exec qbittorrent python3 /app/qbit-manager/main.py --tag-summary

# Tag existing torrents by location (dry run)
docker compose exec qbittorrent python3 /app/qbit-manager/main.py --tag-existing --dry-run

# Actually apply tags
docker compose exec qbittorrent python3 /app/qbit-manager/main.py --tag-existing
```

### **🔧 Administrative Commands**
```bash
# Clear queue (emergency)
docker compose exec qbittorrent python3 /app/qbit-manager/main.py --clear-queue
```

### **Development**
```bash
# Edit scripts locally - changes are live immediately!
vim qbit-manager/pyrosimple-manager/core.py

# No container restart needed for script changes
# (Container restart only needed for dependency changes)
```

## Troubleshooting

### **Package Installation Issues**
```bash
# Check mod logs
docker compose logs qbittorrent | grep -E "(install|pip|python)"

# Force package reinstall
docker compose up -d qbittorrent --force-recreate
```

### **Script Issues**
```bash
# Check script permissions
docker compose exec qbittorrent ls -la /app/qbit-manager/

# Test script execution
docker compose exec qbittorrent python3 /app/qbit-manager/main.py --help
```

### **qBittorrent API Issues**
```bash
# Test API connection
docker compose exec qbittorrent python3 -c "
import qbittorrentapi
client = qbittorrentapi.Client(host='localhost', port=8080, username='admin', password='adminadmin')
client.auth_log_in()
print('API connection successful!')
print(f'Version: {client.app.version}')
"
```

## Migration Notes

If you're upgrading from the old custom Docker build approach:

1. **No data loss**: All existing torrents and settings are preserved
2. **No build step**: Remove any custom build commands
3. **Same functionality**: All features work exactly as before
4. **Better performance**: Faster startup and easier development

See `DOCKER_MODS_MIGRATION.md` for detailed migration information. 