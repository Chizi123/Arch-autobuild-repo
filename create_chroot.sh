#!/bin/bash

#Where to download the image from
BOOTSTRAP_SOURCE=mirror.rackspace.com/archlinux/iso/latest/
BOOTSTRAP_FILE=archlinux-bootstrap-*-x86_64.tar.gz
#Directory for the chroot, default is the same folder as this script
CHDIR=$(dirname "$(realpath $0)")/chroot
#Your username, this is for the chroot user. Needs to be a user on your system
#Need to change the username in all the other files
USER=joel
#Locale for chroot as it appears in /etc/locale.gen. This needs to be the same as the host system's locale
LOCALE=en_US.UTF-8 UTF-8
#Git details
GIT_USER=Chizi123
GIT_EMAIL=joelgrun@gmail.com

#Download bootstrap image
cd /tmp
echo wget -r --no-parent -A \'$BOOTSTRAP_FILE\' http://$BOOTSTRAP_SOURCE | bash
tar xzf $BOOTSTRAP_SOURCE/$BOOTSTRAP_FILE
mv root.x86_64/ $CHDIR
rm -r $BOOTSTRAP_SOURCE

#set up image in directory
cd $CHDIR
sed -i 's/^#Server/Server/' etc/pacman.d/mirrorlist
mkdir home/$USER
echo $USER:x:1000:1000:$USER:/home/$USER:/bin/bash >> etc/passwd
echo $USER::14871:::::: >> etc/shadow
mkdir -p build/repo
ln ../build.sh build/repo/build.sh
echo $LOCALE >> etc/locale.gen
mount --bind $CHDIR $CHDIR
bin/arch-chroot . bash -c "locale-gen;
						   pacman-key --init;
						   pacman-key --populate archlinux;
						   pacman -Syu --noconfirm base-devel vim git;
						   chown $USER:users /home/$USER;
						   su $USER -c \"git config --global credential.helper store;
						   	  		   	 git config --global user.name $GIT_USER;
						   				 git config --global user.email $GIT_EMAIL\";"
echo "$USER ALL=(ALL) NOPASSWD:ALL" >> etc/sudoers
echo "Please set up the remote git repo for hosting"
echo "Navagate to /build/repo to init the repository"
echo "Press ctrl+d when finished"
bin/arch-chroot . su $USER
umount $CHDIR
