#!/bin/sh
# Destructively prepare one explicitly identified USB disk as the local backup
# filesystem. This script intentionally refuses inferred or partially matched
# device identities.
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

usage() {
    cat >&2 <<'EOF'
Usage:
  prepare_backup_drive.sh \
    --device /dev/sdX \
    --expected-serial SERIAL \
    --expected-size-bytes BYTES

The script erases the whole device after displaying its identity and requiring
the exact confirmation phrase shown at the prompt.
EOF
    exit 2
}

trim() {
    sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

device=
expected_serial=
expected_size_bytes=

while [ "$#" -gt 0 ]; do
    case "$1" in
        --device)
            [ "$#" -ge 2 ] || usage
            device=$2
            shift 2
            ;;
        --expected-serial)
            [ "$#" -ge 2 ] || usage
            expected_serial=$2
            shift 2
            ;;
        --expected-size-bytes)
            [ "$#" -ge 2 ] || usage
            expected_size_bytes=$2
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

[ -n "$device" ] || usage
[ -n "$expected_serial" ] || usage
[ -n "$expected_size_bytes" ] || usage

case "$expected_size_bytes" in
    *[!0-9]*|'') usage ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: this script must run as root" >&2
    exit 1
fi

resolved_device=$(readlink -f "$device")
if [ ! -b "$resolved_device" ]; then
    echo "ERROR: not a block device: $device" >&2
    exit 1
fi

if [ "$(lsblk -dno TYPE "$resolved_device" | trim)" != "disk" ]; then
    echo "ERROR: target must be a whole disk, not a partition: $resolved_device" >&2
    exit 1
fi

actual_serial=$(lsblk -dno SERIAL "$resolved_device" | trim)
actual_size_bytes=$(lsblk -bdno SIZE "$resolved_device" | trim)
actual_transport=$(lsblk -dno TRAN "$resolved_device" | trim)
actual_model=$(lsblk -dno MODEL "$resolved_device" | trim)

if [ "$actual_serial" != "$expected_serial" ]; then
    echo "ERROR: serial mismatch: expected '$expected_serial', found '$actual_serial'" >&2
    exit 1
fi

if [ "$actual_size_bytes" != "$expected_size_bytes" ]; then
    echo "ERROR: capacity mismatch: expected '$expected_size_bytes', found '$actual_size_bytes'" >&2
    exit 1
fi

if [ "$actual_transport" != "usb" ]; then
    echo "ERROR: target is not reported as a USB disk: $resolved_device" >&2
    exit 1
fi

root_source=$(findmnt -n -o SOURCE /)
root_source=$(readlink -f "$root_source")
root_parent=$(lsblk -no PKNAME "$root_source" | head -n 1 | trim)
if [ -n "$root_parent" ]; then
    root_disk=$(readlink -f "/dev/$root_parent")
else
    root_disk=$root_source
fi

if [ "$resolved_device" = "$root_disk" ]; then
    echo "ERROR: refusing to erase the disk containing the root filesystem" >&2
    exit 1
fi

if lsblk -nrpo MOUNTPOINT "$resolved_device" | grep -q '[^[:space:]]'; then
    echo "ERROR: the target or one of its partitions is mounted" >&2
    lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS "$resolved_device" >&2
    exit 1
fi

if grep -Eq '[[:space:]]/mnt/bbb-backup[[:space:]]' /etc/fstab; then
    echo "ERROR: /etc/fstab already contains a /mnt/bbb-backup entry" >&2
    exit 1
fi

echo
echo "Verified destructive target"
echo "---------------------------"
printf 'Device:       %s\n' "$resolved_device"
printf 'Model:        %s\n' "$actual_model"
printf 'Serial:       %s\n' "$actual_serial"
printf 'Size (bytes): %s\n' "$actual_size_bytes"
printf 'Transport:    %s\n' "$actual_transport"
echo
echo "Current device layout:"
lsblk -o NAME,PATH,SIZE,TYPE,MODEL,SERIAL,TRAN,FSTYPE,LABEL,UUID,MOUNTPOINTS "$resolved_device"
echo
echo "Current partition table:"
sfdisk --dump "$resolved_device" 2>&1 || true
echo
echo "Current filesystem signatures:"
wipefs --no-act "$resolved_device" 2>&1 || true
for child in $(lsblk -nrpo NAME "$resolved_device" | tail -n +2); do
    wipefs --no-act "$child" 2>&1 || true
done
echo

confirmation="ERASE $expected_serial"
printf 'This permanently destroys every file on %s.\n' "$resolved_device"
printf 'Type exactly: %s\n> ' "$confirmation"
IFS= read -r answer
if [ "$answer" != "$confirmation" ]; then
    echo "Confirmation did not match; nothing was changed." >&2
    exit 1
fi

for child in $(lsblk -nrpo NAME "$resolved_device" | tail -n +2); do
    wipefs --all --force "$child"
done
wipefs --all --force "$resolved_device"

printf 'label: gpt\nstart=2048, type=L\n' |
    sfdisk --wipe always --wipe-partitions always "$resolved_device"
partprobe "$resolved_device"
udevadm settle

case "$resolved_device" in
    *[0-9]) partition="${resolved_device}p1" ;;
    *) partition="${resolved_device}1" ;;
esac

attempt=0
while [ ! -b "$partition" ] && [ "$attempt" -lt 10 ]; do
    sleep 1
    attempt=$((attempt + 1))
done
if [ ! -b "$partition" ]; then
    echo "ERROR: new partition did not appear: $partition" >&2
    exit 1
fi

mkfs.ext4 -F -m 0 -L bbb-backup "$partition"
udevadm settle

filesystem_uuid=$(blkid -s UUID -o value "$partition")
if [ -z "$filesystem_uuid" ]; then
    echo "ERROR: could not read the new filesystem UUID" >&2
    exit 1
fi

install -d -o root -g root -m 0700 /mnt/bbb-backup
fstab_backup="/etc/fstab.bak.bbb-backup.$(date -u +%Y%m%dT%H%M%SZ)"
cp -a /etc/fstab "$fstab_backup"
printf 'UUID=%s /mnt/bbb-backup ext4 defaults,nofail,noatime,x-systemd.device-timeout=10s 0 2\n' \
    "$filesystem_uuid" >> /etc/fstab

mount /mnt/bbb-backup
chown root:root /mnt/bbb-backup
chmod 0700 /mnt/bbb-backup
sync

mounted_uuid=$(findmnt -n -o UUID --target /mnt/bbb-backup)
if [ "$mounted_uuid" != "$filesystem_uuid" ]; then
    echo "ERROR: mounted filesystem UUID does not match the formatted filesystem" >&2
    exit 1
fi

echo
echo "Backup drive prepared successfully."
printf 'Filesystem: %s\n' "$partition"
printf 'Label:      bbb-backup\n'
printf 'UUID:       %s\n' "$filesystem_uuid"
printf 'Mountpoint: /mnt/bbb-backup\n'
printf 'fstab copy: %s\n' "$fstab_backup"
