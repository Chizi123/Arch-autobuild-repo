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

for d in 'emacs-git'
do
	if [ "$d" = "x86_64" ]; then
		continue
	fi
	cd $d
	git pull
	makepkg
	latest=$(newest_matching_file '*.pkg.tar.xz')
	cd ..
	rsync $d/$latest x86_64/$latest
	repo-add ./repo.db.tar.xz x86_64/$latest
done

git add x86_64
git commit -m "'$(date +%d/%m/%y)'"
git push
