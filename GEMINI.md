# FireGuard Project Instructions

## Architecture & Conventions
- **Path Management:** All file paths must be resolved via `server.utils.paths`. 
  - `APP_ROOT`: Location of the executable or entry script.
  - `RESOURCE_ROOT`: Location of bundled assets (e.g., `sys._MEIPASS` in PyInstaller).
  - `STORAGE_DIR`: Persistent writable data (next to the executable).
- **Qt Bindings:** The project strictly uses `PySide6`. `QT_API` and `QT_LIB` environment variables are forced to `pyside6` in `app.py` to prevent conflicts with libraries like `pyqtgraph`.
- **Database:** SQLite with WAL mode enabled. Default timeout is set to 30s to prevent locking issues in bundled environments.
- **Logging:** Centralized rotating file logging in `server/utils/logger.py`. Logs are stored in `APP_ROOT/logs/server.log`.

## Deployment Workflow (Windows)
1. **Build:** Use `pyinstaller FireGuard_fixed.spec --noconfirm`.
   - This bundles FastAPI, Uvicorn, and other hidden dependencies.
   - Applies the Red "F" icon (`fireguard.ico`) to the binary.
2. **Package:** Use Inno Setup with `FireGuard_Setup.iss`.
   - Generates a professional installer `FireGuard_Installer_v1.0.exe`.
   - Automatically excludes local `storage/` and `logs/` to ensure a fresh start for users.
   - Sets folder permissions to allow the app to write its database.
   - Includes the `JETSON_SETUP_GUIDE.txt`.

## Deployment Workflow (Edge/Jetson)
- **Distribution:** Edge code is hosted on GitHub.
- **Installation:** Users run a one-line installer:
  `curl -sSL https://raw.githubusercontent.com/[USER]/[REPO]/main/install.sh | bash`
- **Features:** The script installs NVIDIA-optimized PyTorch, system dependencies, and sets up a `systemd` background service.
