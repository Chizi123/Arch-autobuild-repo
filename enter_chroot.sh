#!/bin/bash

CHDIR=$(dirname "$(realpath $0)")/chroot 
$CHDIR/bin/arch-chroot $CHDIR su joel 
