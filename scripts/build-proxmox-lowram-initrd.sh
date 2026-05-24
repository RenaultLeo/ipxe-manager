#!/usr/bin/env bash
# Build initrd-netboot.img for Proxmox PXE on low-RAM clients (4 GiB+).
# iPXE loads only kernel + this initrd; ISO is wget'd in initramfs via isourl=.
# Based on https://github.com/tohara/ProxmoxPxeBoot (MIT).
set -euo pipefail

INITRD_IN="${1:?usage: build-proxmox-lowram-initrd.sh <initrd.img> <proxmox.iso> <output-initrd>}"
ISO_PATH="${2:?}"
INITRD_OUT="${3:?}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CUSTOM_SBIN_DIR="${REPO_ROOT}/app/data/proxmox_netboot/sbin"

REQUIRED_CMDS=(cpio gzip find dd od tr grep awk unsquashfs)
for cmd in "${REQUIRED_CMDS[@]}"; do
 if ! command -v "${cmd}" >/dev/null 2>&1; then
 echo "[ERROR] Missing required command: ${cmd}" >&2
 exit 1
 fi
done

if [[ ! -f "${INITRD_IN}" ]]; then
 echo "[ERROR] Initrd not found: ${INITRD_IN}" >&2
 exit 1
fi

if [[ ! -f "${ISO_PATH}" ]]; then
 echo "[ERROR] ISO not found: ${ISO_PATH}" >&2
 exit 1
fi

if [[ ! -d "${CUSTOM_SBIN_DIR}" ]]; then
 echo "[ERROR] Custom sbin folder not found: ${CUSTOM_SBIN_DIR}" >&2
 exit 1
fi

WORK_DIR="$(mktemp -d -t pve-lowram-XXXXXX)"
INITRD_UNPACK_DIR="${WORK_DIR}/initrd"
SQUASH_EXTRACT_DIR="${WORK_DIR}/squash"
OUT_DIR="${WORK_DIR}/out"

cleanup() {
 rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

mkdir -p "${INITRD_UNPACK_DIR}" "${SQUASH_EXTRACT_DIR}" "${OUT_DIR}"
mkdir -p "$(dirname "${INITRD_OUT}")"

INITRD_MAGIC="$(dd if="${INITRD_IN}" bs=4 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')"
case "${INITRD_MAGIC}" in
 28b52ffd)
 INITRD_COMPRESS="zstd"
 ;;
 1f8b08*)
 INITRD_COMPRESS="gzip"
 ;;
 *)
 echo "[ERROR] Unsupported initrd compression (magic=${INITRD_MAGIC})" >&2
 exit 1
 ;;
esac

if [[ "${INITRD_COMPRESS}" = "zstd" ]] && ! command -v zstd >/dev/null 2>&1; then
 echo "[ERROR] initrd is zstd-compressed, but 'zstd' is missing" >&2
 exit 1
fi

echo "[INFO] Unpacking initrd (${INITRD_COMPRESS})"
(
 cd "${INITRD_UNPACK_DIR}"
 if [[ "${INITRD_COMPRESS}" = "zstd" ]]; then
 zstd -dc "${INITRD_IN}" | cpio -idmu --quiet
 else
 gzip -dc "${INITRD_IN}" | cpio -idmu --quiet
 fi
)

SQUASH_PATH=""
if command -v bsdtar >/dev/null 2>&1; then
 ISO_LIST_FILE="${WORK_DIR}/iso-list.txt"
 bsdtar -tf "${ISO_PATH}" > "${ISO_LIST_FILE}"
 SQUASH_PATH="$(awk 'BEGIN{IGNORECASE=1} /(^|\/)pve-installer\.squashfs$/ {print; exit}' "${ISO_LIST_FILE}")"
 if [[ -n "${SQUASH_PATH}" ]]; then
 echo "[INFO] Extracting modules from ${SQUASH_PATH}"
 bsdtar -xf "${ISO_PATH}" -C "${WORK_DIR}" "${SQUASH_PATH}"
 EXTRACTED_SQUASH="${WORK_DIR}/${SQUASH_PATH}"
 if [[ -f "${EXTRACTED_SQUASH}" ]]; then
 unsquashfs -f -d "${SQUASH_EXTRACT_DIR}" "${EXTRACTED_SQUASH}" usr/lib/modules >/dev/null
 if [[ -d "${SQUASH_EXTRACT_DIR}/usr/lib/modules" ]]; then
 mkdir -p "${INITRD_UNPACK_DIR}/lib/modules"
 cp -a "${SQUASH_EXTRACT_DIR}/usr/lib/modules/." "${INITRD_UNPACK_DIR}/lib/modules/"
 fi
 fi
 fi
else
 echo "[WARN] bsdtar missing — skipping extra kernel modules from squashfs"
fi

echo "[INFO] Installing network-boot scripts"
mkdir -p "${INITRD_UNPACK_DIR}/sbin"
cp -a "${CUSTOM_SBIN_DIR}/." "${INITRD_UNPACK_DIR}/sbin/"
chmod +x "${INITRD_UNPACK_DIR}/sbin/"*.sh 2>/dev/null || true

INIT_FILE="${INITRD_UNPACK_DIR}/init"
if [[ ! -f "${INIT_FILE}" ]]; then
 echo "[ERROR] init not found in unpacked initrd" >&2
 exit 1
fi

if ! grep -Fq 'sh /sbin/network-fetch-boot-assets.sh' "${INIT_FILE}"; then
 awk '
 !done && /^if \[ -n "\$lvm2root" \]; then$/ {
 print "sh /sbin/network-fetch-boot-assets.sh"
 print ""
 done=1
 }
 { print }
 ' "${INIT_FILE}" > "${INIT_FILE}.tmp"
 mv "${INIT_FILE}.tmp" "${INIT_FILE}"
fi

if ! grep -Fq '. /sbin/installer-cdrom-setup.sh' "${INIT_FILE}"; then
 if grep -Fq 'if ! mount --bind /mnt /mnt/.installer-mp/cdrom; then' "${INIT_FILE}"; then
 awk '
 BEGIN { skip=0; replaced=0 }
 {
 if (!replaced && $0 ~ /^[[:space:]]*if ! mount --bind \/mnt \/mnt\/\.installer-mp\/cdrom; then[[:space:]]*$/) {
 print " . /sbin/installer-cdrom-setup.sh"
 skip=1
 replaced=1
 next
 }
 if (skip) {
 if ($0 ~ /^[[:space:]]*fi[[:space:]]*$/) {
 skip=0
 next
 }
 next
 }
 print
 }
 ' "${INIT_FILE}" > "${INIT_FILE}.tmp"
 mv "${INIT_FILE}.tmp" "${INIT_FILE}"
 else
 awk '
 !done && /^ cp \/etc\/hostid \/mnt\/\.installer-mp\/etc\/$/ {
 print " . /sbin/installer-cdrom-setup.sh"
 print ""
 done=1
 }
 { print }
 ' "${INIT_FILE}" > "${INIT_FILE}.tmp"
 mv "${INIT_FILE}.tmp" "${INIT_FILE}"
 fi
fi

chmod +x "${INIT_FILE}"

echo "[INFO] Repacking initrd (gzip for iPXE)"
(
 cd "${INITRD_UNPACK_DIR}"
 find . -print0 | cpio --null -o -H newc | gzip -9 > "${OUT_DIR}/initrd-netboot.img"
)

cp "${OUT_DIR}/initrd-netboot.img" "${INITRD_OUT}"
echo "[INFO] Wrote ${INITRD_OUT}"
