REPODIR=/repo/x86_64
BUILDDIR=/repo/build
REPONAME=
COMPRESSION=zst
export PKGEXT='.pkg.tar.zst'
export SRCEXT='.src.tar.bz2'
export PACKAGER="John Doe <jd@change.me>"
TO_EMAIL=""
FROM_EMAIL="$(whoami)@$(localhost)"
EMAIL_HOST="server_url:port"
EMAIL_USER="user:pass"
UPDATE=N
PARALLEL=N
QUIET=N
CLEAN=N
SIGN=N
KEY=""
NUM_OLD=5
