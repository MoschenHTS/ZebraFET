# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = [
    ('resources', 'resources'),   # icons, fonts, themes, docs — all under resources/
    ('src',       'src'),         # Python source (needed for dynamic imports)
]
datas += copy_metadata('numpy')
datas += copy_metadata('pandas')

# Collect all PySide6 files including Qt platform plugins and DLLs (required on Windows)
pyside6_datas, pyside6_binaries, pyside6_hiddenimports = collect_all('PySide6')
datas    += pyside6_datas
binaries  = pyside6_binaries
hiddenimports = ['pandas', 'numpy', 'matplotlib', 'markdown'] + pyside6_hiddenimports

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if sys.platform == 'darwin':
    _icon = 'resources/icons/fishapp_icon.icns'
elif sys.platform == 'win32':
    _icon = 'resources/icons/fishapp_icon.ico'
else:
    _icon = 'resources/icons/fishapp_icon.png'

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ZebraFET',
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
    icon=[_icon],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ZebraFET',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='ZebraFET.app',
        icon='resources/icons/fishapp_icon.icns',
        bundle_identifier='com.zebralab.zebrafet',
        info_plist={
            'CFBundleDocumentTypes': [
                {
                    'CFBundleTypeExtensions': ['zfet'],
                    'CFBundleTypeName': 'ZebraFET Project Archive',
                    'CFBundleTypeRole': 'Editor',
                    'CFBundleTypeIconFile': 'fishapp_icon',
                    'LSItemContentTypes': ['com.zebralab.zebrafet.zfet'],
                }
            ],
            'UTExportedTypeDeclarations': [
                {
                    'UTTypeIdentifier': 'com.zebralab.zebrafet.zfet',
                    'UTTypeDescription': 'ZebraFET Project Archive',
                    'UTTypeConformsTo': ['com.pkware.zip-archive'],
                    'UTTypeTagSpecification': {
                        'com.apple.ostype': 'Zfet',
                        'public.filename-extension': ['zfet'],
                        'public.mime-type': 'application/zip',
                    },
                }
            ],
        },
    )
