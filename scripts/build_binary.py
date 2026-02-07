#!/usr/bin/env python3
"""
Script to build a standalone executable for archbuild using PyInstaller.
"""

import subprocess
import sys
import os
from pathlib import Path

def build():
    print("Building standalone executable...")
    
    # Ensure pyinstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    # Path to the source root
    root = Path(__file__).parent.parent
    src = root / "src"
    
    # Create a temporary entry script
    entry_script = root / "archbuild_entry.py"
    entry_script.write_text("from archbuild.cli import main\nif __name__ == '__main__':\n    main()\n")

    # PyInstaller command
    cmd = [
        "pyinstaller",
        "--onefile",
        "--name", "archbuild-bin",
        "--paths", str(src),
        "--clean",
        "--collect-all", "archbuild",
        str(entry_script)
    ]
    
    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=root)
        if result.returncode == 0:
            print("\nSuccessfully built executable: dist/archbuild-bin")
        else:
            print("\nBuild failed!")
            sys.exit(result.returncode)
    finally:
        if entry_script.exists():
            entry_script.unlink()

if __name__ == "__main__":
    build()
