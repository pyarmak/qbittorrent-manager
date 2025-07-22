#!/usr/bin/env python3
"""
Logging configuration for qbit-manager script.
Provides centralized logging with both console and file output.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Try to import config for log settings, use defaults if not available
try:
    import config
    LOG_LEVEL = getattr(config, 'LOG_LEVEL', 'INFO')
    LOG_FILE = getattr(config, 'LOG_FILE', None)
except ImportError:
    LOG_LEVEL = 'INFO'
    LOG_FILE = None

class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to console output"""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',    # Cyan
        'INFO': '\033[32m',     # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',    # Red
        'CRITICAL': '\033[35m', # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record):
        # Add color to the level name for console output
        if hasattr(sys.stderr, 'isatty') and sys.stderr.isatty():
            levelname = record.levelname
            if levelname in self.COLORS:
                record.levelname = f"{self.COLORS[levelname]}{levelname}{self.RESET}"
        
        return super().format(record)

def setup_logging(name='qbit-manager', level=None, log_file=None):
    """
    Set up logging configuration
    
    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file (optional)
    
    Returns:
        logging.Logger: Configured logger instance
    """
    if level is None:
        level = LOG_LEVEL
    
    if log_file is None:
        log_file = LOG_FILE
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    logger.handlers = []
    
    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stderr)
    console_formatter = ColoredFormatter(
        '%(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        try:
            # Create log directory if it doesn't exist
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Add file handler
            file_handler = logging.FileHandler(log_file)
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
            
            logger.debug(f"Logging to file: {log_file}")
        except Exception as e:
            logger.warning(f"Failed to create log file {log_file}: {e}")
    
    return logger

# Create default logger instance
logger = setup_logging()

# Convenience functions for module-level logging
def debug(msg, *args, **kwargs):
    """Log debug message"""
    logger.debug(msg, *args, **kwargs)

def info(msg, *args, **kwargs):
    """Log info message"""
    logger.info(msg, *args, **kwargs)

def warning(msg, *args, **kwargs):
    """Log warning message"""
    logger.warning(msg, *args, **kwargs)

def error(msg, *args, **kwargs):
    """Log error message"""
    logger.error(msg, *args, **kwargs)

def critical(msg, *args, **kwargs):
    """Log critical message"""
    logger.critical(msg, *args, **kwargs)

def exception(msg, *args, **kwargs):
    """Log exception with traceback"""
    logger.exception(msg, *args, **kwargs)

if __name__ == "__main__":
    # Test logging
    test_logger = setup_logging('test', 'DEBUG')
    test_logger.debug("This is a debug message")
    test_logger.info("This is an info message")
    test_logger.warning("This is a warning message")
    test_logger.error("This is an error message")
    test_logger.critical("This is a critical message")
    
    try:
        1 / 0
    except Exception:
        test_logger.exception("This is an exception with traceback") 