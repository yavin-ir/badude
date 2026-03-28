# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['badude/client/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[('badude/client/static', 'static')],
    hiddenimports=['badude', 'badude.protocol', 'badude.dns_codec', 'badude.client', 'badude.client.dns_client', 'badude.client.web'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='badude-client',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
