#!/bin/bash

CHDIR=$(dirname "$(realpath $0)")/chroot
mount --bind $CHDIR $CHDIR
$CHDIR/bin/arch-chroot $CHDIR su joel -c "/build/repo/build.sh"
umount $CHDIR
