import os
import sys

def get_resource_path(relative_path):
    """
    Get the absolute path to a resource. 
    Works for development and for PyInstaller's bundle.
    """
    if getattr(sys, 'frozen', False):
        # If the application is run as a bundle (PyInstaller)
        # sys._MEIPASS is a temporary folder where the app is unpacked
        # In 'onedir' mode, it's just the folder containing the exe.
        base_path = sys._MEIPASS
    else:
        # If the application is run in development
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    return os.path.join(base_path, relative_path)

def get_bin_path(binary_name):
    """
    Resolve paths for portable binaries (Redis, Postgres, Tesseract).
    Priority: 
    1. Local bin/ folder (for testing bundling)
    2. System installations (for dev flexibility)
    """
    # Try the bundled/local bin folder first
    local_path = get_resource_path(os.path.join("bin", binary_name))
    
    # In frozen mode, prefer bundled binary, but allow system fallback if missing
    if getattr(sys, 'frozen', False):
        if os.path.exists(local_path):
            return local_path
        if "redis-server" in binary_name:
            return "C:\\redis\\redis-server.exe"
        elif "tesseract" in binary_name:
            return "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
        return binary_name

    # In development, check if the local bin folder actually has the file (or dir)
    if os.path.exists(local_path):
        return local_path
    
    # Fallbacks for system-installed tools during development
    if "redis-server" in binary_name:
        return "C:\\redis\\redis-server.exe"
    elif "tesseract" in binary_name:
        return "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
    
    return binary_name
