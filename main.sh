#!/bin/bash
#A basic bash script to automate the building of arch packages
# Usage: main.sh init|add|build_all [-f force]

source $(dirname "$(realpath $0)")/vars.sh

#Helper for finding newest and oldest files
#Sourced from stack overflow
# Usage: newold_matching_file [n/o] [filename]
function newold_matching_file
{
    # Use ${1-} instead of $1 in case 'nounset' is set
    local -r glob_pattern=${2-}

    # To avoid printing garbage if no files match the pattern, set
    # 'nullglob' if necessary
    local -i need_to_unset_nullglob=0
    if [[ ":$BASHOPTS:" != *:nullglob:* ]] ; then
        shopt -s nullglob
        need_to_unset_nullglob=1
    fi

    file=
    for f in $glob_pattern ; do
		if [ $1 == "n" ]; then
			[[ -z $f || $f -nt $_file ]] && file=$f
		elif [ $1 == "o" ]; then
			[[ -z $f || $f -ot $_file ]] && file=$f
		fi
    done

    # To avoid unexpected behaviour elsewhere, unset nullglob if it was
    # set by this function
    (( need_to_unset_nullglob )) && shopt -u nullglob

    # Use printf instead of echo in case the file name begins with '-'
    [[ -n $file ]] && printf '%s\n' "$file"

    return 0
}

#Build latest version of a package
# Usage: build_pkg [package name] [-f force]
function build_pkg {
	#check if PKGBUILD has updated, don't rebuild if hasn't changed
	if [[ ! -z $(git pull | grep "Already up to date.") && -z $(echo $1 | grep git) && -z $2 ]]; then
		return 2
	fi

	#remove old versions before build
	rm *$1*.pkg.tar.*

	#make and force rebuild if is git package
	# Mictosoft fonts have problems with checksums and need a seperate argument
	if [[ "$1" == "ttf-ms-win10" ||
		"$1" == "ttf-office-2007-fonts" ||
		"$1" == "ttf-ms-win8" ||
		"$1" == "ttf-win7-fonts" ]]; then
		makepkg -s --noconfirm $([[ $CLEAN == "Y" ]] && echo "-c") $([[ $SIGN == "Y" ]] && echo "--sign --key $KEY") $([[ "$2" == "-f" ]] && echo -f) --skipchecksums
	else
		makepkg -s --noconfirm $([[ $CLEAN == "Y" ]] && echo "-c") $([[ $SIGN == "Y" ]] && echo "--sign --key $KEY") $([[ "$2" == "-f" ]] && echo -f) 2>&1
	fi
	if [[ $? != 0 ]]; then
		#Register error
		echo $1 >> $REPODIR/.errors
		return 1
	fi

	#Get build artifact names
	source PKGBUILD
	pkgs=()
	for i in ${pkgname[@]}; do
		pkgs+=("$i-$pkgver-$pkgrel")
	done

	#Move package to repodir and add to repo db
	for i in ${pkgs[@]}; do
			 rm $REPODIR/$i*.pkg.tar.??*
			 cp $i*.pkg.tar.?? $REPODIR/
			 [[ "$SIGN" == "Y" ]] && cp $i*.pkg.tar.??.sig $REPODIR/
	done

	# Weird exceptions
	if [[ "$1" == "zoom" ]]; then
		rm zoom*_orig*.pkg.tar.xz
	fi

	# Add package to waiting list to be added to repo db
	while true; do
		if [[ $(cat $REPODIR/.waitlist.lck) == 1 ]]; then
			sleep 1
		else
			echo 1 > $REPODIR/.waitlist.lck
			echo $1 >> $REPODIR/.waitlist
			echo 0 > $REPODIR/.waitlist.lck
			break
			fi
	done
	while true; do
		# Wait until package is at the top of the queue and add to db
		if [[ "$(head -n1 $REPODIR/.waitlist)" == "$1" ]]; then
			for i in ${pkgs[@]}; do
				repo-add $([[ "$SIGN" == "Y" ]] && echo "--sign --key $KEY") $REPODIR/$REPONAME.db.tar.xz $REPODIR/$i*.pkg.tar.??
			done
			while true; do
				if [[ $(cat $REPODIR/.waitlist.lck) == 1 ]]; then
					sleep 1
				else
					# Remove self from top of queue
					echo 1 > $REPODIR/.waitlist.lck
					tail -n +2 $REPODIR/.waitlist > $REPODIR/.waitlist.tmp
					mv $REPODIR/.waitlist.tmp $REPODIR/.waitlist
					echo 0 > $REPODIR/.waitlist.lck
					break
				fi
			done
			break
		else
			if [[ -z "$(grep $1 $REPODIR/.waitlist)" ]]; then
				if [[ $(cat $REPODIR/.waitlist.lck) == 1 ]]; then
					sleep 1
				else
					echo 1 > $REPODIR/.waitlist.lck
					echo $1 >> $REPODIR/.waitlist
					echo 0 > $REPODIR/.waitlist.lck
				fi
			fi
			sleep 10
		fi
	done

	#Remove old versions of packages
	#TODO: Want to be able to keep multiple versions of old packages, future work
	#Currently old package versions stay in the repodir indefinately
	# while [ $NUM_OLD \< $(find . -name '*.pkg.tar.xz' | wc -l) ]
	# do
	# 	old=$(newold_matching_file o '*.pkg.tar.xz')
	# 	rm $REPODIR/$old $old
	# done
	return 0
}

#Update packages in BUILDDIR
# Usage: build_all [-f force]
function build_all {
	#system update
	if [[ $UPDATE == "Y" ]]; then
		sudo pacman -Syu --noconfirm
	fi

	#Remove waitlist and errors from old builds
	rm -f $REPODIR/{.waitlist,.errors}
	#update every package currently stored
	for d in $(find $BUILDDIR -maxdepth 1 -mindepth 1 -type d)
	do
		cd $d
		if [[ "$PARALLEL" == "Y" ]]; then
			build_pkg $(echo $d | rev | cut -d'/' -f1 | rev) $1 &> $([[ "$QUIET" == "Y" ]] && echo "/dev/null" || echo "/dev/tty")  &
		else
			build_pkg $(echo $d | rev | cut -d'/' -f1 | rev) $1 &> $([[ "$QUIET" == "Y" ]] && echo "/dev/null" || echo "/dev/tty")
		fi
	done
	wait

	return 0
}

#Add a new package to be built
#There is no name checking so be sure to put in the name correctly
# Usage: add [package name]
function add {
	for i in $@; do
		cd $BUILDDIR
		git clone https://aur.archlinux.org/$i.git
		cd $i
		build_pkg $i -f
	done
	return 0
}

#Remove a package from the build list and repository
# Usage remove [package name]
function remove {
	for i in $@; do
		rm -rf $BUILDDIR/$i*
		repo-remove $REPODIR/$REPONAME.db.tar.xz $i
		rm $REPODIR/$i*
	done
}

#Check config and create build folders
#Set variables before usage
# Usage: init
function init {
	if [[ $uid != 1 ]]; then
		echo "This must be run as root"
	fi

	#check for configuration here
	[[ -z $REPODIR ]] && echo "Enter REPODIR" && return 1
	[[ -z $BUILDDIR ]] && echo "Enter BUILDDIR" && return 2
	[[ -z $REPONAME ]] && echo "Enter REPONAME" && return 3

	#make build directories
	[[ ! -d $REPODIR ]] && mkdir -p $REPODIR
	[[ ! -d $BUILDDIR ]] && mkdir -p $BUILDDIR

	#packages required to build others
	pacman -S --noconfirm base-devel git

	#add repo to pacman.conf so can install own packages
	if [[ -z $(grep "$REPONAME" /etc/pacman.conf) ]]; then
		printf "[$REPONAME]\nSigLevel = Optional TrustAll\nServer = file://$REPODIR\n" >> /etc/pacman.conf
	fi

	#create GPG key for package signing
	if [[ "$SIGN" == "Y" && "$KEY" == "" ]]; then
		(
			echo "Key-Type: RSA"
			echo "Key-Length: 2048"
			echo "Subkey-Type: RSA"
			echo "Subkey-Length: 2048"
			echo "Passphrase: \"\""
			echo "Expire-Date: 0"
			echo "Name-Real: John Doe"
			echo "Name-Comment: Arch buildbot"
			echo "Name-Email: $(whoami)@localhost"
			echo "%commit"
		) | gpg --batch --generate-key
		gpg --export --output $REPONAME.key --armor "John Doe"
		gpg --export-secret-keys --output $REPONAME.secret.key --armor "John Doe"
		echo "Please change the key information in this file"
	fi

	return 0
}

function send_email {
	(
	echo "From: build@localhost"
	echo "To: $EMAIL"
	echo "Subject: Build errors"
	echo "There were build errors for the build of $REPONAME at $(date), please address them soon."
	echo "The errors were: $@"
	) | sendmail -t
}

case $1 in
	"init")
		init;;
	"add")
		add ${@:2};;
	"build-all")
		build_all $([[ "$2" == "-f" ]] && echo "-f");;
	"remove")
		remove ${@:2};;
	*)
		printf "Invalid usage\nUsage: $0 init|add|build-all\n";;
esac

# Error reporting, send email only for build-all as assuming an batch job for that
if [[ -f $REPODIR/.errors ]]; then
	ERRORS=$(cat $REPODIR/.errors | tr '\n' ' ')
	rm $REPODIR/.errors
	echo "Errors in packages: $ERRORS"
	if [[ "$EMAIL" != "" && "$1" == "build-all" ]]; then
		send_email $ERRORS
	fi
else
	echo "All packages built successfully"
fi

