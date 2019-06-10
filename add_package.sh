#!/bin/bash

CHDIR=$(dirname "$(realpath $0)")/chroot 
$CHDIR/bin/arch-chroot $CHDIR su joel -c "cd /build/repo; git clone $1"
