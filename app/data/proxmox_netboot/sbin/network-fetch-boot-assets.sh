#!/bin/sh
# Adapted from ProxmoxPxeBoot (MIT) — https://github.com/tohara/ProxmoxPxeBoot

ISOURL=
ANSWERURL=

# shellcheck disable=SC2013
for par in $(cat /proc/cmdline); do
 case $par in
 isourl=*)
 ISOURL="${par#isourl=}"
 ;;
 answerurl=*)
 ANSWERURL="${par#answerurl=}"
 ;;
 esac
done

IFACE=
for i in /sys/class/net/*; do
 i="${i##*/}"
 [ "$i" = "lo" ] && continue
 ip link set "$i" up 2>/dev/null || true
 if [ -r "/sys/class/net/$i/carrier" ] && [ "$(cat "/sys/class/net/$i/carrier" 2>/dev/null)" = "1" ]; then
 IFACE="$i"
 break
 fi
done

if [ -n "$IFACE" ]; then
 echo "[initrd] Found interface: $IFACE"
 ip link set "$IFACE" up 2>/dev/null || true

 echo "[initrd] DHCP on $IFACE..."
 udhcpc -i "$IFACE" -q -t 10 -T 2 -s /sbin/udhcpc.script || echo "[initrd] DHCP failed"
else
 echo "[initrd] No connected NIC interface found."
fi

if [ -n "$ISOURL" ]; then
 echo "[initrd] Fetching ISO from $ISOURL"
 if wget "$ISOURL" -O /proxmox.iso; then
 echo "[initrd] ISO downloaded successfully"
 else
 echo "[initrd] ERROR: Failed to download ISO"
 fi
fi

if [ -n "$ANSWERURL" ]; then
 mkdir -p /auto
 cat > /auto/auto-installer-mode.toml << 'EOF'
mode = "iso"
partition_label = "proxmox-ais"

[http]
EOF

 echo "[initrd] Fetching answer file from $ANSWERURL"
 if wget "$ANSWERURL" -O /auto/answer.toml; then
 echo "[initrd] Answer file downloaded successfully"
 else
 echo "[initrd] ERROR: Failed to download answer file"
 fi
fi

if [ -n "$IFACE" ]; then
 echo "[initrd] Removing NIC settings on $IFACE"
 ip addr flush dev "$IFACE" 2>/dev/null || true
 ip route flush dev "$IFACE" 2>/dev/null || true
 ip link set "$IFACE" down 2>/dev/null || true
fi
