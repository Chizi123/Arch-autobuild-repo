#!/bin/bash

CHDIR=$(dirname "$(realpath $0)")/chroot
mount --bind $CHDIR $CHDIR
$CHDIR/bin/arch-chroot $CHDIR su joel -c "find /build/repo/dependencies -maxdepth 1 -not -path '*\/.*' -type d -exec git -C {} fetch \; -exec git -C {} reset --hard origin/master \;
					   		  	 	  	  find /build/repo -maxdepth 1 -not -path '*\/.*' -type d -exec git -C {} fetch \; -exec git -C {} reset --hard origin/master \;"
umount $CHDIR
