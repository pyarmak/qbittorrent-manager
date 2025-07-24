#!/bin/bash

# qBittorrent notification script for qbit-manager service
# 
# Configure this script in qBittorrent:
# Settings > Downloads > Run external program on torrent completed:
# /scripts/qbittorrent-notification.sh "%I" "%N" "%L" "%F" "%R" "%D" %C %Z "%G" "%T" "%J" "%K"
#
# Parameters explanation:
# %I = Info hash v1 (SHA-1, 40 chars) - Primary hash for compatibility
# %N = Torrent name
# %L = Category
# %F = Content path (absolute path of torrent content)
# %R = Root path (first torrent subdirectory path, empty if no root)
# %D = Save path (where files are stored)
# %C = Number of files
# %Z = Torrent size (bytes)
# %G = Tags (separated by comma)
# %T = Current tracker
# %J = Info hash v2 (SHA-256, 64 chars, optional)
# %K = Torrent ID (qBittorrent internal ID)

# Configuration
API_KEY="${QBIT_MANAGER_API_KEY}"

SERVICE_URL="${QBIT_MANAGER_URL}"

# Extract parameters in order
HASH_V1="$1"        # %I - Info hash v1 (primary)
NAME="$2"           # %N - Torrent name
CATEGORY="$3"       # %L - Category
CONTENT_PATH="$4"   # %F - Content path
ROOT_PATH="$5"      # %R - Root path
SAVE_PATH="$6"      # %D - Save path
NUM_FILES="$7"      # %C - Number of files
SIZE="$8"           # %Z - Torrent size (bytes)
TAGS="$9"           # %G - Tags (comma-separated)
TRACKER="${10}"     # %T - Current tracker
HASH_V2="${11}"     # %J - Info hash v2 (optional)
TORRENT_ID="${12}"  # %K - Torrent ID

# Validate required parameters
if [ -z "$HASH_V1" ]; then
    echo "ERROR: Missing required hash parameter (%I)" >&2
    exit 1
fi

if [ -z "$NAME" ]; then
    echo "ERROR: Missing required name parameter (%N)" >&2
    exit 1
fi

if [ -z "$API_KEY" ] || [ "$API_KEY" = "your-api-key-here" ]; then
    echo "ERROR: API key not configured. Set HTTP_API_KEY or configure in config.toml" >&2
    exit 1
fi

# Build optimized JSON payload with all qBittorrent parameters
# This allows the service to process without making any API calls
JSON_PAYLOAD=$(cat <<EOF
{
  "hash": "$HASH_V1",
  "params": {
    "hash": "$HASH_V1",
    "hash_v1": "$HASH_V1",
    "hash_v2": "$HASH_V2",
    "torrent_id": "$TORRENT_ID",
    "name": "$NAME",
    "category": "$CATEGORY",
    "content_path": "$CONTENT_PATH",
    "root_path": "$ROOT_PATH",
    "save_path": "$SAVE_PATH",
    "num_files": $NUM_FILES,
    "size": $SIZE,
    "tags": "$TAGS",
    "tracker": "$TRACKER"
  }
}
EOF
)

# Send notification to service
echo "Notifying qbit-manager service for torrent: $NAME"
RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" \
  -X POST \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "$JSON_PAYLOAD" \
  "$SERVICE_URL/notify/torrent-finished")

# Parse response
HTTP_CODE=$(echo "$RESPONSE" | tail -n1 | cut -d: -f2)
BODY=$(echo "$RESPONSE" | sed '$d')

# Check result
if [ "$HTTP_CODE" = "200" ]; then
    echo "SUCCESS: Torrent notification sent successfully"
    echo "Response: $BODY"
    exit 0
else
    echo "ERROR: Failed to notify service (HTTP $HTTP_CODE)"
    echo "Response: $BODY"
    exit 1
fi 