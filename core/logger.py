import logging
import sys
import os

def setup_logger(name="Jarvis", log_file="jarvis.log", level=logging.INFO):
    """Function to setup as many loggers as you want"""
    
    formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')
    
    # File Handler
    handler = logging.FileHandler(log_file)        
    handler.setFormatter(formatter)
    
    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Remove existing handlers to avoid duplicates during reloads
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(handler)
    logger.addHandler(console_handler)
    
    return logger

# Singleton instance
logger = setup_logger()
