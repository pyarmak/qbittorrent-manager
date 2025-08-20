#!/bin/bash
#
# Sonarr/Radarr Import Script for qBittorrent Manager
#
# This script creates symlinks from SSD cache to the Sonarr/Radarr media folders,
# allowing immediate media availability while qbit-manager performs background copying.
#
# Installation:
# 1. Copy this script to your Sonarr/Radarr container
# 2. Make it executable: chmod +x /path/to/sonarr-radarr-import.sh  
# 3. In Sonarr/Radarr Settings -> Media Management -> Importing:
#    - Enable "Import using script"
#    - Set "Import Script Path" to this script's path
#
# Usage: This script is called automatically by Sonarr/Radarr during import
# Arguments: $1 = Source path (SSD), $2 = Destination path (Arr's expected location)
#

set -euo pipefail

# Validate arguments
if [ $# -ne 2 ]; then
    echo "ERROR: Script requires exactly 2 arguments: SOURCE DEST" >&2
    echo "Usage: $0 <source_path> <destination_path>" >&2
    exit 1
fi

SOURCE="$1"
DEST="$2"

# Log for debugging (optional - remove if not needed)
# echo "$(date): Import script called with SOURCE='$SOURCE' DEST='$DEST'" >> /tmp/import-script.log

# Validate source exists
if [ ! -e "$SOURCE" ]; then
    echo "ERROR: Source path does not exist: $SOURCE" >&2
    exit 1
fi

# Create destination directory structure
mkdir -p "$(dirname "$DEST")"

# Remove existing file/link at destination if present
# if [ -e "$DEST" ] || [ -L "$DEST" ]; then
#     rm -f "$DEST"
# fi

# Create symbolic link from source (SSD) to destination (Arr's expected location)
ln -s "$SOURCE" "$DEST"

# Verify symlink was created successfully
if [ ! -L "$DEST" ]; then
    echo "ERROR: Failed to create symlink at: $DEST" >&2
    exit 1
fi

# Verify symlink points to correct source
LINK_TARGET=$(readlink "$DEST")
if [ "$LINK_TARGET" != "$SOURCE" ]; then
    echo "ERROR: Symlink points to wrong target. Expected: $SOURCE, Got: $LINK_TARGET" >&2
    exit 1
fi

# Tell Sonarr/Radarr the final file path (required output format)
echo "[MediaFile] $DEST"

# Optional: Log successful operation
echo "$(date): Successfully created symlink: $DEST -> $SOURCE" >> /config/log/import-script.log

exit 0
