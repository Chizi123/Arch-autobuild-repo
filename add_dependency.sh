#!/bin/bash

CHDIR=$(dirname "$(realpath $0)")/chroot 
RUSER=joelgrun
RLOC=35.225.177.191
RPATH=/var/www/joelg.cf/html/

mount --bind $CHDIR $CHDIR
$CHDIR/bin/arch-chroot $CHDIR su joel -c "cd /build/repo/dependencies;
					   		  	 	  	  git clone https://aur.archlinux.org/$1.git
										  cd $1;
										  makepkg -si --noconfirm;
										  ln $1-*.pkg.tar.xz ../../x86_64/;
										  cd ../../x86_64;
										  #rm Chizi123.db Chizi123.files;
										  repo-add ../Chizi123.db.tar.xz $1-*.pkg.tar.xz;
										  #ln ../Chizi123.db.tar.xz Chizi123.db;
										  #ln ../Chizi123.files.tar.xz Chizi123.files;
										  cd ../;
										  rsync -aL --delete x86_64 $RUSER@$RLOC:$RPATH
										  git add x86_64/;
										  git commit -m \"added $1\";
										  git push"
umount $CHDIR
