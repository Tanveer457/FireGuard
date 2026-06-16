# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect all submodules from our server package and critical dependencies
hidden_imports = collect_submodules('server')
hidden_imports += collect_submodules('uvicorn')
hidden_imports += collect_submodules('fastapi')
hidden_imports += collect_submodules('starlette')
hidden_imports += collect_submodules('websockets')

# Add other hidden dependencies that PyInstaller might miss
hidden_imports += [
    'multipart',
    'PySide6.QtSvg',
    'PySide6.QtXml',
    'paramiko',
    'cryptography',
    'bcrypt',
    'pyqtgraph',
    'cv2',
    'yaml',
    'sqlite3',
    'anyio._backends._asyncio', # Critical for FastAPI/Uvicorn
]

# Collect data files from relevant packages
datas = [
    ('server/assets', 'server/assets'),
]
datas += collect_data_files('fastapi')

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FireGuard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='fireguard.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FireGuard',
)
