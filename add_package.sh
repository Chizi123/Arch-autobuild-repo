#!/bin/bash

CHDIR=$(dirname "$(realpath $0)")/chroot 
mount --bind $CHDIR $CHDIR
$CHDIR/bin/arch-chroot $CHDIR su joel -c "cd /build/repo;
					   		  	 	  	  git clone https://aur.archlinux.org/$1.git
										  cd $1;
										  makepkg -s --noconfirm;
										  ln $1-*.pkg.tar.xz ../x86_64/;
										  cd ../x86_64;
										  rm Chizi123.db Chizi123.files;
										  repo-add ../Chizi123.db.tar.xz $1-*.pkg.tar.xz;
										  ln ../Chizi123.db.tar.xz Chizi123.db;
										  ln ../Chizi123.files.tar.xz Chizi123.files;
										  cd ../;
										  git add x86_64/;
										  git commit -m \"added $1\";
										  git push"
umount $CHDIR
