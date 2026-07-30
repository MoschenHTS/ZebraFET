# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_all, copy_metadata

sys.path.insert(0, '.')
from src.core.constants import APP_VERSION

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

# ── Windows version resource ──────────────────────────────────────────────
# Stamp the exe with publisher/version metadata (drawn from APP_VERSION, the
# single source of truth in src/core/constants.py). An unsigned exe carrying
# proper version info trips fewer AV heuristics. Windows-only; the version file
# is generated as text so it needs no pefile/pywin32 at spec-parse time.
_version_file = None
if sys.platform == 'win32':
    import re as _re
    _nums = [int(_n) for _n in _re.findall(r'\d+', APP_VERSION)][:4]
    _nums += [0] * (4 - len(_nums))
    _vt = tuple(_nums)
    _version_file = 'ZebraFET_version_info.txt'
    with open(_version_file, 'w', encoding='utf-8') as _vf:
        _vf.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={_vt},
    prodvers={_vt},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Henrique Tamanini S. Moschen'),
        StringStruct('FileDescription', 'ZebraFET'),
        StringStruct('FileVersion', '{_APP_VERSION}'),
        StringStruct('InternalName', 'ZebraFET'),
        StringStruct('LegalCopyright', 'Copyright (C) Henrique Tamanini S. Moschen. GPL-3.0.'),
        StringStruct('OriginalFilename', 'ZebraFET.exe'),
        StringStruct('ProductName', 'ZebraFET'),
        StringStruct('ProductVersion', '{_APP_VERSION}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ZebraFET',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[_icon],
    version=_version_file,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
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
            # Names the macOS application menu and the Finder entry. Without
            # these the menu title falls back to the launching executable.
            'CFBundleName': 'ZebraFET',
            'CFBundleDisplayName': 'ZebraFET',
            'CFBundleShortVersionString': APP_VERSION,
            'CFBundleVersion': APP_VERSION,
            'NSHighResolutionCapable': True,
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
