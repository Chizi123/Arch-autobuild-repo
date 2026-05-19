# Maintainer: Joel <joelgrun@gmail.com>

pkgname=archrepobuild
pkgver=2.0.0
pkgrel=1
pkgdesc="Automatic AUR package building and repository management (Python rewrite)"
arch=('any')
url="https://github.com/joelgrun/archrepobuild"
license=('MIT')
depends=(
    'python'
    'python-click'
    'python-yaml'
    'python-pydantic'
    'python-aiohttp'
    'python-rich'
)
makedepends=(
    'python-build'
    'python-installer'
    'python-hatchling'
)
_pkgname=Arch-autobuild-repo
source=("$_pkgname::git+https://github.com/Chizi123/Arch-autobuild-repo.git#branch=python")
# Since we are in the repo, we can use local source for local build
# For actual deployment, this would be a URL and checksum would be needed
sha256sums=('SKIP')

build() {
    cd "$srcdir/$_pkgname"
    python -m build --wheel --no-isolation
}

package() {
    cd "$srcdir/$_pkgname"
    python -m installer --destdir="$pkgdir" dist/*.whl
}
