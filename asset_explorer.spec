# -*- mode: python ; coding: utf-8 -*-
"""Asset Explorer 单文件 exe 打包配置。"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("frontend", "frontend")]
binaries = []
hiddenimports = []

# UnityPy 及其 C 扩展解码器、Web 框架：收集子模块 + 数据 + 二进制
for pkg in [
    "UnityPy", "brotli", "etcpak", "astc_encoder", "texture2ddecoder",
    "lz4", "fmod_toolkit", "pyfmodex", "PIL", "archspec",
    "uvicorn", "fastapi", "starlette", "pydantic", "pydantic_core",
    "multipart", "websockets", "httptools",
]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("UnityPy")
hiddenimports += [
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "encodings", "backend", "backend.main",
]

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy.f2py", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AssetExplorer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)