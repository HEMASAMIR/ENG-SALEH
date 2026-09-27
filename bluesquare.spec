# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# Collect data files
added_files = [
    ('ui/qss', 'ui/qss'),
    ('db/schema.sql', 'db'),
    ('db/seed.sql', 'db'),
    ('bluesquare.ico', '.'),
    ('bin/redis', 'bin/redis'),
    ('bin/tesseract', 'bin/tesseract'),
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=added_files,
    hiddenimports=[
        'sqlalchemy.ext.declarative',
        'sqlalchemy.orm',
        'sqlalchemy.engine.url',
        'redis',
        'cv2',
        'numpy',
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'gxipy',
        'pymodbus',
        'pymodbus.client',
        'pymodbus.client.serial',
        'serial',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['ultralytics', 'torch', 'torchvision', 'yolo_service.py'],
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
    name='BlueSquareApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True, # Set to False for windowed mode after testing
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['bluesquare.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BlueSquare',
)
