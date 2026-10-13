# main.py
"""
Main entry point for Scraping & Monitoring System
"""
import sys
import os
from core.logger import setup_logger, suppress_http_logging

setup_logger("global", "scraper")
suppress_http_logging()

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.shutdown_handler import get_shutdown_handler
from menu.cli_menu import menu


if __name__ == "__main__":
    # Initialize shutdown handler FIRST (before anything else)
    shutdown_handler = get_shutdown_handler()
    
    try:
        menu()
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupt received, shutting down...")
        shutdown_handler.shutdown()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        shutdown_handler.shutdown(1)


 