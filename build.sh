#!/bin/bash

function newest_matching_file
{
    # Use ${1-} instead of $1 in case 'nounset' is set
    local -r glob_pattern=${1-}

    if (( $# != 1 )) ; then
        echo 'usage: newest_matching_file GLOB_PATTERN' >&2
        return 1
    fi

    # To avoid printing garbage if no files match the pattern, set
    # 'nullglob' if necessary
    local -i need_to_unset_nullglob=0
    if [[ ":$BASHOPTS:" != *:nullglob:* ]] ; then
        shopt -s nullglob
        need_to_unset_nullglob=1
    fi

    newest_file=
    for file in $glob_pattern ; do
        [[ -z $newest_file || $file -nt $newest_file ]] \
            && newest_file=$file
    done

    # To avoid unexpected behaviour elsewhere, unset nullglob if it was
    # set by this function
    (( need_to_unset_nullglob )) && shopt -u nullglob

    # Use printf instead of echo in case the file name begins with '-'
    [[ -n $newest_file ]] && printf '%s\n' "$newest_file"

    return 0
}

#update system
sudo pacman -Syu --noconfirm

#go to build directory
cd $(dirname "$(realpath $0)")

#Remove old packages
git rm -r x86_64/*
mkdir x86_64

#dependencies
cd dependencies
for d in `find . -maxdepth 1 -not -path '*/\.*' -type d`
do
	#Only do package directories
	if [ "$d" = "./x86_64" ] || [ "$d" = "." ]; then
		continue
	fi
	cd $d
	#update package to latest from AUR
	git pull
	makepkg -si --noconfirm
	latest=$(newest_matching_file '*.pkg.tar.xz')
	cd ..
	ln $d/$latest ../x86_64/$latest
	repo-add ../Chizi123.db.tar.xz ../x86_64/$latest
done
cd ..

#main packages
for d in `find . -maxdepth 1 -not -path '*/\.*' -type d`
do
	#Only do package directories
	if [ "$d" = "./x86_64" ] || [ "$d" = "." ] || [ "$d" = "dependencies" ]; then
		continue
	fi
	cd $d
	#update package to latest from AUR
	git pull
	makepkg -s --noconfirm
	latest=$(newest_matching_file '*.pkg.tar.xz')
	cd ..
	ln $d/$latest x86_64/$latest
	repo-add ./Chizi123.db.tar.xz x86_64/$latest
done

ln Chizi123.db.tar.xz x86_64/Chizi123.db
ln Chizi123.files.tar.xz x86_64/Chizi123.files
git add x86_64
git commit -m "'$(date +%d/%m/%y-%H:%M)'"
git push