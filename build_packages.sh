#!/bin/bash

CHDIR=$(dirname "$(realpath $0)")/chroot 
$CHDIR/bin/arch-chroot $CHDIR su joel -c "/build/repo/build.sh"
