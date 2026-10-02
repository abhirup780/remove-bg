# PyInstaller spec: one-folder windowed build -> dist\BG Remove\BG Remove.exe
# Build:  .venv\Scripts\python -m PyInstaller bgremove.spec --noconfirm
a = Analysis(
    ["app.py"],
    datas=[("ui", "ui"), ("models", "models"), ("bgremove.ico", ".")],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "setuptools"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="BG Remove",
    icon="bgremove.ico",
    console=False,
    version="version_info.txt",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="BG Remove", upx=False)
