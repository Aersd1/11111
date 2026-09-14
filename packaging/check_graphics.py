"""Report the exact missing native library before trying to start Qt."""
import ctypes
from pathlib import Path
import subprocess
from PySide6.QtCore import QLibraryInfo

plugin = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / 'platforms/libqxcb.so'
subprocess.run(['ldd', str(plugin)], check=True)
ctypes.CDLL(str(plugin))
print('Native XCB plugin dependencies load successfully.')
