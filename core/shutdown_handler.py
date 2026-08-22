# core/shutdown_handler.py
"""
Global shutdown handler for graceful cleanup
Ensures all pending data is flushed before exit
"""
import signal
import sys
import time
import threading
from typing import List, Callable, Dict, Any
from datetime import datetime

from core.logger import setup_logger

logger = setup_logger("shutdown_handler", "core")


class ShutdownHandler:
    """
    Handles graceful shutdown with automatic data flushing
    """
    
    def __init__(self):
        self.shutdown_in_progress = False
        self.shutdown_hooks: List[Callable] = []
        self.flush_hooks: List[Dict] = []
        self._original_handlers = {}
        self._lock = threading.Lock()
        
    def register_flush_hook(self, hook: Callable, name: str = None):
        """
        Register a hook to be called during shutdown to flush data
        """
        with self._lock:
            self.flush_hooks.append({
                'func': hook,
                'name': name or f"hook_{len(self.flush_hooks)}"
            })
            logger.debug(f"Flush hook registered: {self.flush_hooks[-1]['name']}")
    
    def register_shutdown_hook(self, hook: Callable, name: str = None):
        """
        Register a hook to be called during shutdown
        """
        with self._lock:
            self.shutdown_hooks.append({
                'func': hook,
                'name': name or f"hook_{len(self.shutdown_hooks)}"
            })
            logger.debug(f"Shutdown hook registered: {self.shutdown_hooks[-1]['name']}")
    
    def flush_all(self) -> bool:
        """
        Flush all pending data
        Returns True if all successful
        """
        if self.shutdown_in_progress:
            return True
        
        logger.info("🔄 FLUSHING ALL PENDING DATA...")
        success = True
        
        for hook in self.flush_hooks:
            try:
                start = time.time()
                logger.info(f"   Flushing: {hook['name']}...")
                result = hook['func']()
                elapsed = time.time() - start
                
                if result:
                    logger.info(f"   ✅ {hook['name']} completed in {elapsed:.2f}s")
                else:
                    logger.warning(f"   ⚠️ {hook['name']} reported partial failure in {elapsed:.2f}s")
                    success = False
                    
            except Exception as e:
                logger.error(f"   ❌ {hook['name']} failed: {e}")
                success = False
        
        logger.info(f"FLUSH_COMPLETE | success={success}")
        return success
    
    def shutdown(self, exit_code: int = 0):
        """
        Perform graceful shutdown with Redis pool cleanup
        """
        if self.shutdown_in_progress:
            return
        
        self.shutdown_in_progress = True
        
        print("\n" + "="*60)
        print("🛑 SHUTTING DOWN...")
        print("="*60)
        
        logger.info("SHUTDOWN_STARTED")
        
        try:
            # First, flush all pending data
            flush_success = self.flush_all()
            
            # Then, call shutdown hooks
            for hook in self.shutdown_hooks:
                try:
                    logger.info(f"   Running shutdown hook: {hook['name']}")
                    hook['func']()
                except Exception as e:
                    logger.error(f"   ❌ Shutdown hook {hook['name']} failed: {e}")
            
            # =====================================================
            # Close Redis connection pool on shutdown
            # =====================================================
            try:
                from Database.Redis.Configuration.redis_client import get_redis_client
                redis_client = get_redis_client()
                if redis_client:
                    # Close the connection pool
                    redis_client.close_pool()
                    logger.info("✅ Redis connection pool closed")
            except ImportError:
                # Redis module might not be available
                pass
            except Exception as e:
                logger.error(f"Redis pool close error: {e}")
            
            print("="*60)
            if flush_success:
                print("✅ All data flushed successfully")
                logger.info("SHUTDOWN_COMPLETE | success=True")
            else:
                print("⚠️ Some data may not have been flushed")
                logger.warning("SHUTDOWN_COMPLETE | success=False")
            print("="*60)
            
        except Exception as e:
            logger.error(f"SHUTDOWN_ERROR | {e}")
            import traceback
            traceback.print_exc()
        
        sys.exit(exit_code)
    
    def _signal_handler(self, signum, frame):
        """Handle SIGINT and SIGTERM"""
        print(f"\n\n⚠️ Received signal: {signum}")
        self.shutdown()
    
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        signals = [signal.SIGINT, signal.SIGTERM]
        
        for sig in signals:
            self._original_handlers[sig] = signal.signal(sig, self._signal_handler)
        
        logger.info("Signal handlers installed for graceful shutdown")


# Global instance
_shutdown_handler = None


def get_shutdown_handler() -> ShutdownHandler:
    """Get or create global shutdown handler"""
    global _shutdown_handler
    if _shutdown_handler is None:
        _shutdown_handler = ShutdownHandler()
        _shutdown_handler.setup_signal_handlers()
    return _shutdown_handler


def register_flush_hook(hook: Callable, name: str = None):
    """Register a flush hook"""
    return get_shutdown_handler().register_flush_hook(hook, name)


def register_shutdown_hook(hook: Callable, name: str = None):
    """Register a shutdown hook"""
    return get_shutdown_handler().register_shutdown_hook(hook, name)


def flush_all():
    """Flush all pending data"""
    return get_shutdown_handler().flush_all()


def shutdown(exit_code: int = 0):
    """Graceful shutdown with Redis pool cleanup"""
    return get_shutdown_handler().shutdown(exit_code)