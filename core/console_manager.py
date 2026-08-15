# core/console_manager.py
"""
Centralized console manager to prevent output collisions
Ensures all terminal output is properly synchronized
"""
import threading
import sys
import subprocess
import os
import platform
import tempfile
import time
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
    WHITE = '\033[97m'


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
        self._console_type = self._detect_console_type()
        self._temp_files = []
    
    def _detect_console_type(self):
        system = platform.system()
        if system == "Windows":
            return "windows"
        elif system == "Darwin":
            return "macos"
        else:
            return "linux"
    
    def _clear_line(self):
        sys.stdout.write('\r' + ' ' * 120 + '\r')
        sys.stdout.flush()
    
    def print(self, message: str, color: str = Colors.END, end: str = "\n", flush: bool = True):
        with self._print_lock:
            self._clear_line()
            if color and color != Colors.END:
                colored_msg = f"{color}{message}{Colors.END}"
            else:
                colored_msg = message
            print(colored_msg, end=end, flush=flush)
            self._buffer.append(message)
            if len(self._buffer) > 1000:
                self._buffer = self._buffer[-500:]
    
    def print_header(self, title: str, color: str = Colors.CYAN):
        self.print("", color)
        self.print("=" * 60, Colors.DIM)
        self.print(f"{color}{Colors.BOLD}{title}{Colors.END}", color)
        self.print("=" * 60, Colors.DIM)
    
    def print_separator(self, char: str = "=", length: int = 60, color: str = Colors.DIM):
        self.print(char * length, color)
    
    def print_success(self, message: str):
        self.print(f"✅ {message}", Colors.GREEN)
    
    def print_error(self, message: str):
        self.print(f"❌ {message}", Colors.RED)
    
    def print_warning(self, message: str):
        self.print(f"⚠️ {message}", Colors.YELLOW)
    
    def print_info(self, message: str):
        self.print(f"ℹ️ {message}", Colors.CYAN)
    
    def print_status(self, site: str, status: str, details: str = ""):
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
        if title:
            self.print_header(title)
        
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(cell)))
        
        header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
        self.print(header_line, Colors.CYAN)
        self.print("-" * len(header_line), Colors.DIM)
        
        for row in rows:
            line = " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row))
            self.print(line)
    
    def show_prompt(self, prompt: str = "> "):
        with self._print_lock:
            self._clear_line()
            sys.stdout.write(f"{Colors.CYAN}{prompt}{Colors.END}")
            sys.stdout.flush()
            self._prompt_active = True
    
    def hide_prompt(self):
        with self._print_lock:
            self._clear_line()
            self._prompt_active = False
    
    def get_buffer(self, limit: int = 100) -> list:
        return self._buffer[-limit:] if self._buffer else []
    
    def open_new_terminal(self, command: str, title: str = "Terminal") -> bool:
        """Open a new terminal window with proper encoding"""
        system = platform.system()
        try:
            # Sanitize title - remove emojis and special characters for Windows
            if system == "Windows":
                # Remove emojis and non-ASCII characters from title
                import re
                sanitized_title = re.sub(r'[^\x00-\x7F]+', '', title)
                sanitized_title = sanitized_title.strip() or "Terminal"
                return self._open_windows_terminal(command, sanitized_title)
            elif system == "Linux":
                return self._open_linux_terminal(command, title)
            elif system == "Darwin":
                return self._open_macos_terminal(command, title)
            else:
                self.print_error(f"Unsupported operating system: {system}")
                return False
        except Exception as e:
            self.print_error(f"Failed to open terminal: {e}")
            return False
    
    def _open_windows_terminal(self, command: str, title: str) -> bool:
        """Open a Windows command prompt - no temp files left behind"""
        import tempfile
        
        # Ensure command is properly encoded for Windows
        try:
            # Encode command to ASCII for Windows compatibility
            command = command.encode('ascii', 'ignore').decode('ascii')
        except:
            pass
        
        # Create a temporary batch file with proper encoding
        with tempfile.NamedTemporaryFile(mode='w', suffix='.bat', delete=False, encoding='utf-8') as f:
            bat_path = f.name
            bat_content = f'''@echo off
chcp 65001 > nul
title {title}
color 0A
echo.
echo ============================================================
echo     {title}
echo ============================================================
echo.
{command}
echo.
echo ============================================================
echo [SUCCESS] Process completed.
echo ============================================================
echo.
echo Press any key to close this window...
pause > nul
'''
            f.write(bat_content)
        
        # Open the batch file
        subprocess.Popen(f'start "{title}" cmd /c "{bat_path}"', shell=True)
        
        # Clean up after the batch file is done (delete after 10 seconds)
        def cleanup():
            time.sleep(10)
            try:
                if os.path.exists(bat_path):
                    os.remove(bat_path)
            except:
                pass
        
        threading.Thread(target=cleanup, daemon=True).start()
        return True
    
    def _open_linux_terminal(self, command: str, title: str) -> bool:
        # Remove emojis for terminal title on Linux
        import re
        sanitized_title = re.sub(r'[^\x00-\x7F]+', '', title)
        sanitized_title = sanitized_title.strip() or "Terminal"
        
        full_command = f'''
echo "============================================================"
echo "    {sanitized_title}"
echo "============================================================"
echo.
{command}
echo.
echo "============================================================"
echo "Process completed."
echo "============================================================"
echo.
echo "Press Enter to close..."
read'''
        
        if self._command_exists("gnome-terminal"):
            subprocess.Popen(["gnome-terminal", "--title", sanitized_title, "--", "bash", "-c", full_command])
            return True
        elif self._command_exists("xterm"):
            subprocess.Popen(["xterm", "-T", sanitized_title, "-e", "bash", "-c", full_command])
            return True
        else:
            self.print_error("No terminal emulator found")
            return False
    
    def _open_macos_terminal(self, command: str, title: str) -> bool:
        # Remove emojis for terminal title on macOS
        import re
        sanitized_title = re.sub(r'[^\x00-\x7F]+', '', title)
        sanitized_title = sanitized_title.strip() or "Terminal"
        
        full_command = f'''
echo "============================================================"
echo "    {sanitized_title}"
echo "============================================================"
echo.
{command}
echo.
echo "============================================================"
echo "Process completed."
echo "============================================================"
echo.
echo "Press any key to close..."
read'''
        
        escaped_command = full_command.replace('"', '\\"')
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{escaped_command}"'])
        return True
    
    def _command_exists(self, cmd: str) -> bool:
        import shutil
        return shutil.which(cmd) is not None
    
    def open_python_script_terminal(self, script_path: str, args: list = None, title: str = "Python Script") -> bool:
        if args is None:
            args = []
        
        if not os.path.exists(script_path):
            self.print_error(f"Script not found: {script_path}")
            return False
        
        # Sanitize title for Windows
        import re
        sanitized_title = re.sub(r'[^\x00-\x7F]+', '', title)
        sanitized_title = sanitized_title.strip() or "Python Script"
        
        python_exe = sys.executable
        command = f'"{python_exe}" "{script_path}" {" ".join(args)}'
        return self.open_new_terminal(command, sanitized_title)
    
    def cleanup_temp_files(self):
        """Clean up any remaining temp files"""
        for file_path in self._temp_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
        self._temp_files = []


_console = None


def get_console() -> ConsoleManager:
    global _console
    if _console is None:
        _console = ConsoleManager()
    return _console


def get_console_manager() -> ConsoleManager:
    return get_console()


def open_new_terminal(command: str, title: str = "Terminal") -> bool:
    return get_console().open_new_terminal(command, title)


def open_python_script_terminal(script_path: str, args: list = None, title: str = "Python Script") -> bool:
    return get_console().open_python_script_terminal(script_path, args, title)