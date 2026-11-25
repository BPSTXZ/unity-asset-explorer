"""路径解析：区分打包后的只读资源目录与可写数据目录。

开发态两者均为项目根目录；PyInstaller 冻结态下资源在 sys._MEIPASS（临时解包、只读），
缓存/导出等可写数据放在 exe 同级目录，保证持久且用户可见。
"""
from __future__ import annotations

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    RESOURCE_DIR = Path(sys._MEIPASS)
    DATA_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = RESOURCE_DIR

CACHE_DIR = DATA_DIR / ".cache"
EXPORT_DIR = DATA_DIR / "exports"
FRONTEND_DIR = RESOURCE_DIR / "frontend"