# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['..\\src\\script_chainer\\win_exe\\launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('../config/project.yml', 'resources/config'),
        ('../config/repository.yml', 'resources/config'),
        ('../assets', 'resources/assets'),
        ('../src/script_chainer/webui/web', 'script_chainer/webui/web'),
    ],
    hiddenimports=[
        'webview',
        'webview.platforms.edgechromium',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # 用户 Python 脚本可能用到的电脑操作能力，运行器里没有静态引用，需显式收集
        'pynput',
        'win32clipboard',
        'one_dragon.base.controller.pc_clipboard',
        'one_dragon.base.controller.pc_button.pc_button_listener',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OneDragon ScriptChainer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
    icon=['..\\assets\\ui\\editor_icon.ico'],
    contents_directory='.runtime',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OneDragon ScriptChainer',
)
