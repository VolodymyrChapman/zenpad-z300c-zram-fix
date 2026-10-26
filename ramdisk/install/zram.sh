#!/system/bin/sh
# zram hook: installed to /system/etc/zram.sh by boot-zram-install.img and
# run by /system/bin/adbon.sh at every boot. It does what the init.zram.rc
# that ASUS left out of the boot image would have done (see fstab.zram and
# init.smallram.rc in the stock ramdisk).

case "$(cat /proc/swaps)" in
*zram0*) exit 0 ;;
esac
[ -e /dev/block/zram0 ] || exit 0

echo 536870912 > /sys/block/zram0/disksize
mkswap /dev/block/zram0 > /dev/null
swapon /dev/block/zram0 || exit 1

# Read one page at a time from swap; prefer compressing idle pages over
# killing background apps
echo 0 > /proc/sys/vm/page-cluster
echo 160 > /proc/sys/vm/swappiness
echo 160 > /sys/fs/cgroup/memory/sw/memory.swappiness

echo "zram hook: 512 MB zram swap enabled" > /dev/kmsg
