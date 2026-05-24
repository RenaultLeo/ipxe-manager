#!/bin/sh
# Adapted from ProxmoxPxeBoot (MIT) — https://github.com/tohara/ProxmoxPxeBoot

mkdir -p /mnt/.workdir/work-cdrom
mkdir -p /mnt/.workdir/upper-cdrom

# Create a writable view of the ISO at /mnt/.installer-mp/cdrom
if ! mount -t overlay -o lowerdir=/mnt,upperdir=/mnt/.workdir/upper-cdrom,workdir=/mnt/.workdir/work-cdrom none /mnt/.installer-mp/cdrom; then
 debugsh_err_reboot "overlay mount cdrom failed"
fi

if [ -f /auto/answer.toml ]; then
 echo "copying auto files to installer environment"
 if ! cp /auto/* /mnt/.installer-mp/cdrom; then
 debugsh_err_reboot "copying auto files failed"
 fi
fi
