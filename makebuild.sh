#!/bin/bash

BUILDDIR=$(dirname "$(realpath $0)")
cd $BUILDDIR

docker run -ti -w /build -v $BUILDDIR/repo:/build joeleg/archbuild ./build.sh
# git add repo/x86_64
# git commit -m "'date =%d/%m/%y-%H:%M'"
# git push
