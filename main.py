#!/usr/bin/env python3
"""
Panda AppStore Bot - Railway Entry Point
"""

import os
import sys
import logging
from bot import main

# Configure logging for Railway
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Starting Panda AppStore Bot on Railway...")
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
