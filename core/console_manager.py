# core/console_manager.py
"""
Centralized console manager to prevent output collisions
Ensures all terminal output is properly synchronized
"""
import threading
import sys
from typing import Optional

# ANSI color codes
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    MAGENTA = '\033[35m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'
    DIM = '\033[2m'


class ConsoleManager:
    """
    Singleton console manager that synchronizes all terminal output
    Prevents text collisions from multiple threads/classes
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(ConsoleManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._print_lock = threading.Lock()
        self._prompt_active = False
        self._last_line = ""
        self._buffer = []
    
    def _clear_line(self):
        """Clear the current line"""
        sys.stdout.write('\r' + ' ' * 120 + '\r')
        sys.stdout.flush()
    
    def print(self, message: str, color: str = Colors.END, end: str = "\n", flush: bool = True):
        """
        Thread-safe print that prevents collisions
        
        Args:
            message: Message to print
            color: ANSI color code
            end: End character (default: newline)
            flush: Flush immediately
        """
        with self._print_lock:
            # Clear current line if prompt is active
            self._clear_line()
            
            # Build colored message
            if color and color != Colors.END:
                colored_msg = f"{color}{message}{Colors.END}"
            else:
                colored_msg = message
            
            # Print with proper ending
            print(colored_msg, end=end, flush=flush)
            
            # Store in buffer
            self._buffer.append(message)
            if len(self._buffer) > 1000:
                self._buffer = self._buffer[-500:]
    
    def print_header(self, title: str, color: str = Colors.CYAN):
        """Print a formatted header"""
        self.print("", color)
        self.print("=" * 60, Colors.DIM)
        self.print(f"{color}{Colors.BOLD}{title}{Colors.END}", color)
        self.print("=" * 60, Colors.DIM)
    
    def print_separator(self, char: str = "=", length: int = 60, color: str = Colors.DIM):
        """Print a separator line"""
        self.print(char * length, color)
    
    def print_success(self, message: str):
        """Print a success message in green"""
        self.print(f"✅ {message}", Colors.GREEN)
    
    def print_error(self, message: str):
        """Print an error message in red"""
        self.print(f"❌ {message}", Colors.RED)
    
    def print_warning(self, message: str):
        """Print a warning message in yellow"""
        self.print(f"⚠️ {message}", Colors.YELLOW)
    
    def print_info(self, message: str):
        """Print an info message in cyan"""
        self.print(f"ℹ️ {message}", Colors.CYAN)
    
    def print_status(self, site: str, status: str, details: str = ""):
        """Print a status message for a site"""
        status_colors = {
            "RUNNING": Colors.GREEN,
            "STARTED": Colors.GREEN,
            "RESUMED": Colors.GREEN,
            "ENABLED": Colors.GREEN,
            "PAUSED": Colors.YELLOW,
            "STOPPED": Colors.YELLOW,
            "DISABLED": Colors.YELLOW,
            "ERROR": Colors.RED,
            "FAILED": Colors.RED,
        }
        color = status_colors.get(status.upper(), Colors.CYAN)
        details_str = f" | {details}" if details else ""
        self.print(f"{site.upper():10} | {status}", color)
    
    def print_table(self, headers: list, rows: list, title: str = None):
        """Print a formatted table"""
        if title:
            self.print_header(title)
        
        # Calculate column widths
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(cell)))
        
        # Print header
        header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
        self.print(header_line, Colors.CYAN)
        self.print("-" * len(header_line), Colors.DIM)
        
        # Print rows
        for row in rows:
            line = " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row))
            self.print(line)
    
    def show_prompt(self, prompt: str = "> "):
        """Show the input prompt"""
        with self._print_lock:
            self._clear_line()
            sys.stdout.write(f"{Colors.CYAN}{prompt}{Colors.END}")
            sys.stdout.flush()
            self._prompt_active = True
    
    def hide_prompt(self):
        """Hide the input prompt"""
        with self._print_lock:
            self._clear_line()
            self._prompt_active = False
    
    def get_buffer(self, limit: int = 100) -> list:
        """Get recent console output"""
        return self._buffer[-limit:] if self._buffer else []


# Global console instance
_console = None


def get_console() -> ConsoleManager:
    """Get the global console manager instance"""
    global _console
    if _console is None:
        _console = ConsoleManager()
    return _console