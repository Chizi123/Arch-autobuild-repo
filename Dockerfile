FROM archlinux/base
MAINTAINER joeleg

#Workaround for wrong permissions
RUN /usr/bin/chmod -v 1777 /tmp

#Update and install software
RUN /usr/bin/pacman -Syu --noconfirm base-devel git sudo go rsync && \
	/usr/sbin/pacman -Scc --noconfirm && \
	/usr/sbin/echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

# Add user, group sudo; switch to user
RUN /usr/sbin/groupadd --system sudo && \
    /usr/sbin/useradd -m --groups sudo user
USER user
