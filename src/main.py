#!/usr/bin/env python3
"""
qBittorrent Manager - Service Mode Only

This script runs the qBittorrent Manager as a persistent HTTP service orchestrator.
All functionality is accessed through the HTTP API endpoints.

Usage:
    python3 main.py [--dry-run]

The service will start and listen for HTTP requests on the configured port.
Use the HTTP API to interact with the service.
"""

import sys
import signal
import argparse

# Import configuration first to validate environment
import config

# Import logging from our logger module
try:
    from logger import setup_logging
    logger = setup_logging('qbit-manager-service')
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('qbit-manager-service')

def signal_handler(sig, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Caught signal {sig} ({signal.Signals(sig).name}). Shutting down gracefully...")
    
    try:
        # Shutdown the service orchestrator
        logger.info("Gracefully shutting down service orchestrator...")
        
        try:
            from service import orchestrator
            orchestrator.shutdown(save_state=True)
            logger.info("Service shutdown complete")
        except Exception as e:
            logger.error(f"Error during service shutdown: {e}")
        
        # Close qBittorrent client if it exists
        try:
            from qbit import close_qbit_client
            close_qbit_client()
        except Exception as e:
            logger.debug(f"Error closing qBittorrent client: {e}")
        
        logger.info("Graceful shutdown complete")
        
    except Exception as e:
        logger.error(f"Error during graceful shutdown: {e}")
        logger.info("Performing emergency shutdown")
    
    sys.exit(0)

def main():
    """Main entry point for the service"""
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='qBittorrent Manager HTTP Service',
        epilog='''
This service provides HTTP API endpoints for managing torrent processing,
space management, and other tasks. All functionality is accessed through
the HTTP API.

API Documentation: See SERVICE_API.md for endpoint details.
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--dry-run', action='store_true', 
                       help='Run in dry-run mode (no actual file operations)')
    
    args = parser.parse_args()
    
    # Set dry-run mode globally
    if args.dry_run:
        config.DRY_RUN = True
        logger.warning("===== DRY RUN MODE - NO CHANGES WILL BE MADE =====")
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Validate configuration before starting
    try:
        if hasattr(config, 'validate_config'):
            errors, warnings = config.validate_config()
            
            for warning in warnings:
                logger.warning(warning)
            
            if errors:
                for error in errors:
                    logger.error(error)
                logger.critical("Configuration errors detected. Exiting.")
                sys.exit(1)
                
    except Exception as e:
        logger.warning(f"Configuration validation failed: {e}")
    
    # Show configuration summary
    if hasattr(config, 'show_config_summary'):
        config.show_config_summary()
    
    # Start the HTTP service
    logger.info("Starting qBittorrent Manager HTTP Service...")
    
    try:
        from service import run_service
        run_service()
    except ImportError as e:
        logger.error(f"Failed to import service module: {e}")
        logger.error("Make sure aiohttp is installed: pip install aiohttp")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Service interrupted by user")
    except Exception as e:
        logger.error(f"Service error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
