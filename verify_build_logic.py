import asyncio
from pathlib import Path
import os
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from archrepobuild.builder import _run_makepkg

async def test_already_built():
    print("Testing 'already built' message handling...")
    
    # Create a mock PKGBUILD directory
    test_dir = Path("test_build_logic")
    test_dir.mkdir(exist_ok=True)
    pkgbuild = test_dir / "PKGBUILD"
    pkgbuild.write_text("pkgname=testpkg\npkgver=1.0\npkgrel=1\narch=('any')\n")
    
    # Manually create a dummy package file to trigger the error if we run twice
    package_file = test_dir / "testpkg-1.0-1-any.pkg.tar.zst"
    package_file.write_text("dummy")
    
    # Run _run_makepkg. It should fail with returncode != 0 but we should catch it.
    # Note: We can't easily mock the subprocess output of makepkg here 
    # but we can check if it behaves correctly when real makepkg is called.
    
    success, error, artifacts = _run_makepkg(test_dir, force=False)
    
    print(f"Success: {success}")
    print(f"Error: {error}")
    print(f"Artifacts: {artifacts}")
    
    if success and "testpkg-1.0-1-any.pkg.tar.zst" in [a.name for a in artifacts]:
        print("✓ Successfully handled 'already built' message")
    else:
        print("✗ Failed to handle 'already built' message (or makepkg didn't report it)")

    # Cleanup
    shutil = __import__('shutil')
    shutil.rmtree(test_dir)

if __name__ == "__main__":
    asyncio.run(test_already_built())
