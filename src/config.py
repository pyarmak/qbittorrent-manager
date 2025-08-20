# ===================================================================
# TOML-Based Configuration for qBittorrent Manager
# 
# This configuration system uses TOML files for better organization
# and easier management compared to environment variables.
# ===================================================================

import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # Fallback for older Python versions
    except ImportError:
        print("ERROR: TOML support not available. Install tomli: pip install tomli")
        sys.exit(1)

# ===================================================================
# Configuration Loading
# ===================================================================

def find_config_file() -> Path:
    """Find the configuration file in order of preference"""
    # Order of preference for config file locations
    config_paths = [
        Path("/config/config.toml"),  # Container mount
        Path("config.toml"),          # Current directory
    ]
    
    for config_path in config_paths:
        if config_path.exists():
            return config_path
    
    raise FileNotFoundError(
        "No configuration file found. Please create config.toml from config.toml.example"
    )

def load_config() -> Dict[str, Any]:
    """Load configuration from TOML file"""
    config_file = find_config_file()
    
    try:
        with open(config_file, 'rb') as f:
            config = tomllib.load(f)
        print(f"Loaded configuration from: {config_file}")
        return config
    except Exception as e:
        print(f"ERROR: Failed to load configuration from {config_file}: {e}")
        sys.exit(1)

# Load the configuration
_config = load_config()

# ===================================================================
# Configuration Access Helpers
# ===================================================================

def get_nested(config: Dict[str, Any], path: str, default=None):
    """Get nested configuration value using dot notation"""
    keys = path.split('.')
    value = config
    
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    
    return value

def get_config(path: str, default=None, required: bool = False):
    """Get configuration value with optional requirement check"""
    value = get_nested(_config, path, default)
    
    if required and value is None:
        raise ValueError(f"Required configuration '{path}' not found")
    
    return value

# ===================================================================
# Environment Variable Overrides
# ===================================================================
# Allow environment variables to override TOML config for Docker compatibility

def get_env_override(env_var: str, config_path: str, default=None, value_type=str):
    """Get value from environment variable or config file"""
    env_value = os.getenv(env_var)
    if env_value is not None:
        if value_type == bool:
            return env_value.lower() in ('true', '1', 'yes', 'on')
        elif value_type == int:
            return int(env_value)
        else:
            return env_value
    
    return get_config(config_path, default)

# ===================================================================
# Application Configuration
# ===================================================================

# --- General Settings ---
PUID = get_env_override('PUID', 'general.puid', 1000, int)
PGID = get_env_override('PGID', 'general.pgid', 1000, int)
LOG_LEVEL = get_env_override('LOG_LEVEL', 'general.log_level', 'INFO').upper()
DRY_RUN = get_env_override('DRY_RUN', 'general.dry_run', False, bool)

# --- Storage Paths ---
DOWNLOAD_PATH_SSD = get_env_override('DOWNLOAD_PATH_SSD', 'paths.downloads.ssd', '/downloads/ssd')
FINAL_DEST_BASE_HDD = get_env_override('FINAL_DEST_BASE_HDD', 'paths.downloads.hdd', '/downloads/hdd')

# --- Configuration Paths ---
CONFIG_BASE = get_env_override('CONFIG_BASE', 'paths.config.base', '/config')
LOG_FILE = get_env_override('LOG_FILE', 'logging.file', f'{CONFIG_BASE}/log/qbit-manager.log')
LOCK_DIR = get_env_override('LOCK_DIR', 'paths.config.state', f'{CONFIG_BASE}/qbit-manager-state')

# --- qBittorrent Connection ---
QBIT_HOST = get_env_override('QBIT_HOST', 'qbittorrent.host', 'localhost')
QBIT_PORT = get_env_override('QBIT_PORT', 'qbittorrent.port', 8080, int)
QBIT_USERNAME = get_env_override('QBIT_USERNAME', 'qbittorrent.username', 'admin')
QBIT_PASSWORD = get_env_override('QBIT_PASSWORD', 'qbittorrent.password', 'adminadmin')
QBIT_VERIFY_SSL = get_env_override('QBIT_VERIFY_SSL', 'qbittorrent.verify_ssl', True, bool)

# --- HTTP Service Configuration ---
HTTP_ENABLED = get_env_override('HTTP_ENABLED', 'http_service.enabled', True, bool)
HTTP_HOST = get_env_override('HTTP_HOST', 'http_service.host', '127.0.0.1')
HTTP_PORT = get_env_override('HTTP_PORT', 'http_service.port', 8081, int)
HTTP_API_KEY = get_env_override('HTTP_API_KEY', 'http_service.api_key', '')

# --- Processing Configuration ---
MAX_CONCURRENT_PROCESSES = get_env_override('MAX_CONCURRENT_PROCESSES', 'processing.concurrency.max_concurrent', 3, int)
DISK_SPACE_THRESHOLD_GB = get_env_override('DISK_SPACE_THRESHOLD_GB', 'processing.storage.threshold_gb', 100, int)
COPY_RETRY_ATTEMPTS = get_env_override('COPY_RETRY_ATTEMPTS', 'processing.copy.retry_attempts', 3, int)
VERIFICATION_ENABLED = get_env_override('VERIFICATION_ENABLED', 'processing.copy.verification_enabled', True, bool)

# --- Performance Configuration ---
MAX_CONCURRENT_COPY_OPERATIONS = get_env_override('MAX_CONCURRENT_COPY_OPERATIONS', 'performance.max_concurrent_copy_operations', 1, int)
COPY_OPERATION_NICE_LEVEL = get_env_override('COPY_OPERATION_NICE_LEVEL', 'performance.copy_operation_nice_level', 10, int)
COPY_BUFFER_SIZE = get_env_override('COPY_BUFFER_SIZE', 'performance.copy_buffer_size', 1048576, int)

# --- Notification Configuration ---
NOTIFY_ARR_ENABLED = get_env_override('NOTIFY_ARR_ENABLED', 'notifications.enabled', True, bool)

# Sonarr Configuration
SONARR_URL = get_env_override('SONARR_URL', 'notifications.sonarr.url', 'http://sonarr:8989')
SONARR_API_KEY = get_env_override('SONARR_API_KEY', 'notifications.sonarr.api_key', '')
SONARR_TAG = get_env_override('SONARR_TAG', 'notifications.sonarr.tag', 'sonarr')

# Radarr Configuration
RADARR_URL = get_env_override('RADARR_URL', 'notifications.radarr.url', 'http://radarr:7878')
RADARR_API_KEY = get_env_override('RADARR_API_KEY', 'notifications.radarr.api_key', '')
RADARR_TAG = get_env_override('RADARR_TAG', 'notifications.radarr.tag', 'radarr')

# --- Storage Location Tags ---
ENABLE_LOCATION_TAGGING = get_env_override('ENABLE_LOCATION_TAGGING', 'storage_tags.enabled', True, bool)
AUTO_TAG_NEW_TORRENTS = get_env_override('AUTO_TAG_NEW_TORRENTS', 'storage_tags.auto_tag_new', True, bool)
SSD_LOCATION_TAG = get_env_override('SSD_LOCATION_TAG', 'storage_tags.ssd_tag', 'ssd')
HDD_LOCATION_TAG = get_env_override('HDD_LOCATION_TAG', 'storage_tags.hdd_tag', 'hdd')

# --- Import Script Mode Configuration ---
ENABLE_IMPORT_SCRIPT_MODE = get_env_override('ENABLE_IMPORT_SCRIPT_MODE', 'import_script.enabled', False, bool)

# --- Sonarr/Radarr Root Folder Paths (for symlink discovery during space management) ---
SONARR_ROOT_FOLDERS = get_env_override('SONARR_ROOT_FOLDERS', 'import_script.sonarr_root_folders', [])
RADARR_ROOT_FOLDERS = get_env_override('RADARR_ROOT_FOLDERS', 'import_script.radarr_root_folders', [])

# Parse comma-separated paths from environment if provided as string
if isinstance(SONARR_ROOT_FOLDERS, str):
    SONARR_ROOT_FOLDERS = [path.strip() for path in SONARR_ROOT_FOLDERS.split(',') if path.strip()]
if isinstance(RADARR_ROOT_FOLDERS, str):
    RADARR_ROOT_FOLDERS = [path.strip() for path in RADARR_ROOT_FOLDERS.split(',') if path.strip()]

# --- Tautulli Configuration (for streaming checks) ---
TAUTULLI_URL = get_env_override('TAUTULLI_URL', 'import_script.tautulli_url', 'http://tautulli:8181')
TAUTULLI_API_KEY = get_env_override('TAUTULLI_API_KEY', 'import_script.tautulli_api_key', '')

# --- Path Mapping Configuration (for different container mounts) ---
# Map local paths to Plex container paths for Tautulli file matching
PLEX_PATH_MAPPINGS = {
    # Default mappings - Local qbit-manager path -> Plex container path
    '/downloads/ssd': '/mnt/ssd-cache/flood',  # SSD cache mapping
    '/downloads/hdd': '/Downloads',            # HDD downloads mapping
}

# Load path mappings from TOML config if available
try:
    path_mappings_config = get_config('import_script.path_mappings', {})
    if path_mappings_config:
        PLEX_PATH_MAPPINGS.update(path_mappings_config)
except Exception:
    # Ignore errors during config loading
    pass

# Allow environment overrides for path mappings (JSON format)
try:
    import json
    env_mappings = os.getenv('PLEX_PATH_MAPPINGS')
    if env_mappings:
        env_path_mappings = json.loads(env_mappings)
        PLEX_PATH_MAPPINGS.update(env_path_mappings)
except (json.JSONDecodeError, TypeError):
    # Ignore JSON parsing errors
    pass
except Exception:
    pass

# ===================================================================
# Validation and Helper Functions
# ===================================================================

def get_qbit_connection_info():
    """Get qBittorrent connection info as a dict for instantiating Client"""
    # Validate configuration
    if not QBIT_USERNAME:
        raise ValueError("qBittorrent username cannot be empty")
    if not QBIT_PASSWORD:
        raise ValueError("qBittorrent password cannot be empty")
    if not QBIT_HOST:
        raise ValueError("qBittorrent host cannot be empty")
    
    # Validate port range
    if not (1 <= QBIT_PORT <= 65535):
        raise ValueError(f"qBittorrent port must be between 1 and 65535, got {QBIT_PORT}")
    
    return {
        'host': f"{QBIT_HOST}:{QBIT_PORT}",
        'username': QBIT_USERNAME,
        'password': QBIT_PASSWORD,
        'VERIFY_WEBUI_CERTIFICATE': QBIT_VERIFY_SSL
    }

# Arr Configuration Helper
ARR_CONFIG = {
    "NOTIFY_ARR_ENABLED": NOTIFY_ARR_ENABLED,
    "SONARR_URL": SONARR_URL,
    "SONARR_API_KEY": SONARR_API_KEY,
    "RADARR_URL": RADARR_URL,
    "RADARR_API_KEY": RADARR_API_KEY
}

def validate_config():
    """Validate configuration values"""
    errors = []
    warnings = []
    
    # Check HTTP service configuration
    if HTTP_ENABLED:
        if not HTTP_API_KEY:
            errors.append("HTTP API key must be set when HTTP service is enabled")
        elif len(HTTP_API_KEY) < 16:
            warnings.append("HTTP API key should be at least 16 characters for security")
        
        # Check port range
        if not (1024 <= HTTP_PORT <= 65535):
            errors.append(f"HTTP port must be between 1024 and 65535, got {HTTP_PORT}")
        
        # Check host binding
        if HTTP_HOST not in ['127.0.0.1', 'localhost', '0.0.0.0']:
            warnings.append(f"HTTP host '{HTTP_HOST}' - ensure this is intentional for security")
    
    # Check notification configuration
    if NOTIFY_ARR_ENABLED:
        if not SONARR_API_KEY:
            warnings.append("Sonarr API key not set - Sonarr notifications will fail")
        if not RADARR_API_KEY:
            warnings.append("Radarr API key not set - Radarr notifications will fail")
    
    # Check location tagging configuration
    if ENABLE_LOCATION_TAGGING:
        if not SSD_LOCATION_TAG or not SSD_LOCATION_TAG.strip():
            errors.append("SSD location tag cannot be empty when location tagging is enabled")
        if not HDD_LOCATION_TAG or not HDD_LOCATION_TAG.strip():
            errors.append("HDD location tag cannot be empty when location tagging is enabled")
        if SSD_LOCATION_TAG == HDD_LOCATION_TAG:
            errors.append("SSD and HDD location tags must be different")
    
    # Check import script mode configuration
    if ENABLE_IMPORT_SCRIPT_MODE:
        if not SONARR_ROOT_FOLDERS and not RADARR_ROOT_FOLDERS:
            warnings.append("Import script mode enabled but no root folders configured - symlink discovery may fail")
        if not TAUTULLI_API_KEY:
            warnings.append("Tautulli API key not set - streaming checks will fail during space management")
        if not TAUTULLI_URL:
            warnings.append("Tautulli URL not set - streaming checks will fail during space management")
        if not PLEX_PATH_MAPPINGS:
            warnings.append("PLEX_PATH_MAPPINGS not configured - may cause path matching issues with Tautulli")
    
    # Check threshold values
    if DISK_SPACE_THRESHOLD_GB < 10:
        warnings.append(f"Disk space threshold ({DISK_SPACE_THRESHOLD_GB} GB) seems very low")
    elif DISK_SPACE_THRESHOLD_GB > 1000:
        warnings.append(f"Disk space threshold ({DISK_SPACE_THRESHOLD_GB} GB) seems very high")
    
    # Check retry attempts
    if COPY_RETRY_ATTEMPTS < 1:
        errors.append("Copy retry attempts must be at least 1")
    elif COPY_RETRY_ATTEMPTS > 10:
        warnings.append(f"Copy retry attempts ({COPY_RETRY_ATTEMPTS}) seems excessive")
    
    # Check concurrent processes limit
    if MAX_CONCURRENT_PROCESSES < 1:
        errors.append("Max concurrent processes must be at least 1")
    elif MAX_CONCURRENT_PROCESSES > 10:
        warnings.append(f"Max concurrent processes ({MAX_CONCURRENT_PROCESSES}) seems excessive")
    
    # Check paths exist (at runtime) - only if we're in a container environment
    if os.path.exists('/config') and os.path.exists('/downloads'):  # Container-specific check
        if not os.path.exists(DOWNLOAD_PATH_SSD):
            errors.append(f"SSD download path '{DOWNLOAD_PATH_SSD}' does not exist")
        elif not os.access(DOWNLOAD_PATH_SSD, os.W_OK):
            errors.append(f"SSD download path '{DOWNLOAD_PATH_SSD}' is not writable")
            
        if not os.path.exists(FINAL_DEST_BASE_HDD):
            errors.append(f"HDD destination path '{FINAL_DEST_BASE_HDD}' does not exist")
        elif not os.access(FINAL_DEST_BASE_HDD, os.W_OK):
            errors.append(f"HDD destination path '{FINAL_DEST_BASE_HDD}' is not writable")
    elif not os.path.exists('/config'):
        # Development/testing environment
        warnings.append("Running outside container environment - path validation skipped")
    
    # Validate log directory
    log_dir = os.path.dirname(LOG_FILE)
    if os.path.exists('/config') and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception as e:
            warnings.append(f"Could not create log directory {log_dir}: {e}")
    
    # Validate state directory
    if os.path.exists('/config') and not os.path.exists(LOCK_DIR):
        try:
            os.makedirs(LOCK_DIR, exist_ok=True)
        except Exception as e:
            warnings.append(f"Could not create state directory {LOCK_DIR}: {e}")
    
    return errors, warnings

def show_config_summary():
    """Display a summary of current configuration"""
    print("=== qBittorrent Manager Configuration ===")
    print(f"Config Source: {find_config_file()}")
    print(f"SSD Path: {DOWNLOAD_PATH_SSD}")
    print(f"HDD Path: {FINAL_DEST_BASE_HDD}")
    print(f"Space Threshold: {DISK_SPACE_THRESHOLD_GB} GB")
    print(f"Retry Attempts: {COPY_RETRY_ATTEMPTS}")
    print(f"Max Concurrent: {MAX_CONCURRENT_PROCESSES}")
    print(f"Verification: {'Enabled' if VERIFICATION_ENABLED else 'Disabled'}")
    print(f"Arr Notifications: {'Enabled' if NOTIFY_ARR_ENABLED else 'Disabled'}")
    print(f"Location Tagging: {'Enabled' if ENABLE_LOCATION_TAGGING else 'Disabled'}")
    if ENABLE_LOCATION_TAGGING:
        print(f"  SSD Tag: '{SSD_LOCATION_TAG}'")
        print(f"  HDD Tag: '{HDD_LOCATION_TAG}'")
        print(f"  Auto-tag New: {'Yes' if AUTO_TAG_NEW_TORRENTS else 'No'}")
    print(f"Log Level: {LOG_LEVEL}")
    print(f"State Directory: {LOCK_DIR}")
    if HTTP_ENABLED:
        print(f"HTTP Service: {HTTP_HOST}:{HTTP_PORT}")
        print(f"API Key: {'Set' if HTTP_API_KEY else 'Not Set'}")
    else:
        print("HTTP Service: Disabled")
    print("=" * 40)

# Run validation when module is imported (but not during tests)
if __name__ != "__main__" and 'pytest' not in os.environ.get('_', ''):
    errors, warnings = validate_config()
    if errors:
        print("ERROR: Configuration issues detected:")
        for error in errors:
            print(f"  - {error}")
    if warnings:
        print("WARNING: Configuration warnings:")
        for warning in warnings:
            print(f"  - {warning}")

# =================================================================== 