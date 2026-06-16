import sys
import os
from pathlib import Path

def get_app_root() -> Path:
    """
    Get the root directory of the application.
    In development, it's the directory containing app.py.
    In PyInstaller, it's the directory where the .exe is located.
    """
    if getattr(sys, 'frozen', False):
        # Running in a PyInstaller bundle
        return Path(sys.executable).parent
    else:
        # Running in normal Python environment
        return Path(__file__).resolve().parent.parent.parent

def get_resource_root() -> Path:
    """
    Get the directory containing bundled resources.
    In PyInstaller, this is sys._MEIPASS.
    In development, it's the same as get_app_root().
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return get_app_root()

def ensure_dir(path: Path) -> Path:
    """Create directory if it doesn't exist."""
    path.mkdir(parents=True, exist_ok=True)
    return path

# --- Standard Paths ---
APP_ROOT = get_app_root()
RESOURCE_ROOT = get_resource_root()

# Persistent Writable Data (next to .exe or in project root)
STORAGE_DIR = ensure_dir(APP_ROOT / "storage")
LOGS_DIR = ensure_dir(APP_ROOT / "logs")
DB_PATH = STORAGE_DIR / "fire.db"

# Bundled Resources (Read-Only)
ASSETS_DIR = RESOURCE_ROOT / "server" / "assets"
STYLES_DIR = ASSETS_DIR / "styles"
DARK_THEME_QSS = STYLES_DIR / "dark_theme.qss"

# Edge Config
EDGE_CONFIG_DIR = APP_ROOT / "edge"
EDGE_CONFIG_PATH = EDGE_CONFIG_DIR / "config.yaml"
