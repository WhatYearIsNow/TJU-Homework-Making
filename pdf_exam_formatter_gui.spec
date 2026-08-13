# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path.cwd()
assets_dir = project_root / "assets"

datas = []
if assets_dir.exists():
    datas.append((str(assets_dir), "assets"))


a = Analysis(
    ["app/gui.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "fitz",
        "pymupdf",
        "PIL._tkinter_finder",
    ],
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
    [],
    exclude_binaries=True,
    name="pdf_exam_formatter_gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="pdf_exam_formatter_gui",
)
