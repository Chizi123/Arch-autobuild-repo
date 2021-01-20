#!/bin/bash
#A basic bash script to automate the building of arch packages
# Usage: main.sh init|check|add|remove|build_all

source $(dirname "$(realpath $0)")/vars.sh

ERRORFILE=$(mktemp)
WAITLIST=$(mktemp)
WAITLIST_LCK=$(mktemp)

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
	if [[ -n $(git pull | grep 'Already up to date.') && -z $(grep 'pkgver() {' PKGBUILD) && -z "$2" ]]; then
		return 2
	fi

	#Remove old packages from build directory
	source PKGBUILD
	srcdir="$(pwd)/src"
	if grep -q 'pkgver() {' PKGBUILD; then
		ver=$(pkgver)
	else
		ver=$pkgver
	fi
	find . -mindepth 1 -maxdepth 1 -type f \( -name "*.pkg.tar.*" -o -name "*.src.tar.*" \) -not -name "*$ver-$pkgrel*" -delete

	#make and force rebuild if is git package
	# Mictosoft fonts have problems with checksums and need a seperate argument
	if [[ "$1" == "ttf-ms-win10" ||
		"$1" == "ttf-office-2007-fonts" ||
		"$1" == "ttf-ms-win8" ||
		"$1" == "ttf-win7-fonts" ]]; then
		makepkg -s --noconfirm $([[ $CLEAN == "Y" ]] && echo "-c") $([[ $SIGN == "Y" ]] && echo "--sign --key $KEY") $([[ "$2" == "-f" ]] && echo -f) --skipchecksums 2>&1
	else
		makepkg -s --noconfirm $([[ $CLEAN == "Y" ]] && echo "-c") $([[ $SIGN == "Y" ]] && echo "--sign --key $KEY") $([[ "$2" == "-f" ]] && echo -f) 2>&1
	fi
	if [[ $? != 0  && $? != 13 ]]; then
		#Register error
		echo $1 >> $ERRORFILE
		return 1
	fi

	#Get build artifact names from PKGBUILD and build artifacts
	#Remove duplicates from the list
	pkgs=()
	ipkgs=()
	for i in ${pkgname[@]}; do
		#pkgs+=("$i-$pkgver-$pkgrel")
		ipkgs+=($(find . -mindepth 1 -maxdepth 1 -type f \( -name "$i*.pkg.tar.*" -o -name "$i*.src.tar.*" \) -not -name "*.sig" | sed 's/^\.\///'))
	done
	while read -r -d '' x; do pkgs+=("$x"); done < <(printf "%s\0" "${ipkgs[@]}" | sort -uz)

	# Weird exceptions
	if [[ "$1" == "zoom" ]]; then
		rm zoom*_orig*
		for i in ${pkgs[@]}; do
			if [ -z "${i##*_orig*}" ]; then
				pkgs=(${pkgs[@]/$i})
			fi
		done
	fi

	#Move package to repodir and add to repo db
	#Dont change the database if rebuilt the same package at same release and version
	flag=0
	for i in ${pkgs[@]}; do
		if [[ ! -f $REPODIR/$i ]]; then
			flag=1
		fi
	done
	if [[ $flag == 1 ]]; then
		rm -f $REPODIR/*$1*.tar.*
		for i in ${pkgs[@]}; do
			cp $i $REPODIR/
			[[ "$SIGN" == "Y" ]] && cp $i.sig $REPODIR/
		done
	else
		return;
	fi

	# Add package to waiting list to be added to repo db
	while true; do
		if [[ $(cat $WAITLIST_LCK) == 1 ]]; then
			sleep 1
		else
			echo 1 > $WAITLIST_LCK
			echo $1 >> $WAITLIST
			echo 0 > $WAITLIST_LCK
			break
			fi
	done
	while true; do
		# Wait until package is at the top of the queue and add to db
		if [[ "$(head -n1 $WAITLIST)" == "$1" ]]; then
		    repo-add $([[ "$SIGN" == "Y" ]] && echo "--sign --key $KEY") $REPODIR/$REPONAME.db.tar.$([ -n "$COMPRESSION" ] || echo $COMPRESSION && echo zst) ${pkgs[@]}
			while true; do
				if [[ $(cat $WAITLIST_LCK) == 1 ]]; then
					sleep 1
				else
					# Remove self from top of queue
					echo 1 > $WAITLIST_LCK
					TEMP=$(mktemp)
					tail -n +2 $WAITLIST > $TEMP
					cp $TEMP $WAITLIST
					rm $TEMP
					unset TEMP
					echo 0 > $WAITLIST_LCK
					break
				fi
			done
			break
		else
			if [[ -z "$(grep $1 $WAITLIST)" ]]; then
				# Not on waitlist for some reason, need to readd
				if [[ $(cat $WAITLIST_LCK) == 1 ]]; then
					sleep 1
				else
					echo 1 > $WAITLIST_LCK
					echo $1 >> $WAITLIST
					echo 0 > $WAITLIST_LCK
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
	#	old=$(newold_matching_file o '*.pkg.tar.xz')
	#	rm $REPODIR/$old $old
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
#Adding build dependencies is
# Usage: add [package name]
function add {
	local i j k
	for i in $@; do
		cd $BUILDDIR
		if [[ -z $(git ls-remote https://aur.archlinux.org/$i.git) ]]; then
			echo "Not a package: $i"
			exit 2
		fi
		git clone https://aur.archlinux.org/$i.git
		cd $i
		unset depends
		unset makedepends
		local makedeps
		source PKGBUILD

		#Check for all build dependencies
		for j in ${makedepends[@]}; do
			k=$(echo $j | sed 's/[>]=.*//g')
			if ! (pacman -Si $k || pacman -Qi $k); then
				makedeps+=($k)
			fi &>/dev/null
		done
		for j in ${depends[@]}; do
			k=$(echo $j | sed 's/[>]=.*//g')
			if ! (pacman -Si $k || pacman -Qi $k); then
				makedeps+=($k)
			fi &>/dev/null
		done

		#Add dependencies and update so overall build can work
		for j in ${makedeps[@]}; do
			add $j
		done
		if [[ -n "${makedeps[@]}" ]]; then
			sudo pacman -Sy
		fi

		#Actually build wanted package
		cd $BUILDDIR/$i
		build_pkg $i -f
	done
	return 0
}

#Remove a package from the build list and repository
#Usage of -a removes all packages moved to official repos
# Usage remove [-a|package name]
function remove {
	if [[ "$1" == "-a" ]]; then
		rmlist=""
		rmlist="$rmlist $(comm -12 <(pacman -Slq $REPONAME | sort) <(pacman -Slq core | sort) | tr '\n' ' ')"
		rmlist="$rmlist $(comm -12 <(pacman -Slq $REPONAME | sort) <(pacman -Slq extra | sort) | tr '\n' ' ')"
		rmlist="$rmlist $(comm -12 <(pacman -Slq $REPONAME | sort) <(pacman -Slq community | sort) | tr '\n' ' ')"
		for i in $rmlist; do
			rm -rf $BUILDDIR/$i
			repo-remove $([[ "$SIGN" == "Y" ]] && echo "--sign --key $KEY") $REPODIR/$REPONAME.db.tar.$([ -n "$COMPRESSION" ] || echo $COMPRESSION && echo zst) $i
			rm -f $REPODIR/*$i*
		done
	else
		for i in $@; do
			rm -rf $BUILDDIR/$i
			repo-remove $([[ "$SIGN" == "Y" ]] && echo "--sign --key $KEY") $REPODIR/$REPONAME.db.tar.$([ -n "$COMPRESSION" ] || echo $COMPRESSION && echo zst) $i
			rm -f $REPODIR/*$i*
		done
	fi
}

#Check for packages moved to official repos or removed from the AUR
function check {
	rmlist=""
	rmlist="$rmlist $(comm -12 <(pacman -Slq $REPONAME | sort) <(pacman -Slq core | sort) | tr '\n' ' ')"
	rmlist="$rmlist $(comm -12 <(pacman -Slq $REPONAME | sort) <(pacman -Slq extra | sort) | tr '\n' ' ')"
	rmlist="$rmlist $(comm -12 <(pacman -Slq $REPONAME | sort) <(pacman -Slq community | sort) | tr '\n' ' ')"
	TMPFILE=$(mktemp)
	for i in $(find $BUILDDIR -mindepth 1 -maxdepth 1 -type d); do
		check_pkg $TMPFILE "$(echo $i | rev | cut -d'/' -f1 | rev)" &
	done
	wait
	echo "Merged into official repos: $rmlist"
	echo "Not in AUR: $(cat $TMPFILE | tr '\n' ' ')"
	rm -f $TMPFILE
}

#Check helper function
function check_pkg {
	if [[ -z "$(curl -sI "https://aur.archlinux.org/packages/$2" | head -n1 | grep 200)" ]]; then
		echo "$2" >> $1
	fi
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
	"check")
		check;;
	*)
		echo -e "\033[0;31mInvalid usage\033[0m"
		echo -e "Usage: $0 init|check|add|remove|build-all"
		echo -e "\033[0;32minit\033[0m                        - initialise repository for use"
		echo -e "\033[0;32mcheck\033[0m                       - check if packages have been moved into the official repositories or removed from the AUR"
		echo -e "\033[0;32madd package ...\033[0m             - add a package to \$BUILDDIR and repository, also used to rebuild failed packages"
		echo -e "\033[0;32mremove -a | package ...\033[0m     - remove package from \$BUILDDIR and repository, \"-a\" removes packages added to official repos"
		echo -e "\033[0;32mbuild-all [-f]\033[0m              - build all packages in \$BUILDDIR, \"-f\" force builds whole repository"
esac

# Error reporting, send email only for build-all as assuming an batch job for that
if [[ $1 == "build-all" || $1 == "add" ]]; then
	if [[ -n $(cat $ERRORFILE) ]]; then
		ERRORS=$(cat $ERRORFILE | tr '\n' ' ')
		echo "Errors in packages: $ERRORS"
		if [[ "$EMAIL" != "" && "$1" == "build-all" ]]; then
			send_email $ERRORS
		fi
	else
		echo "All packages built successfully"
	fi
fi

rm $ERRORFILE $WAITLIST $WAITLIST_LCK
