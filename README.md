# ZenPad 10 (Z300C) zram fix

ASUS's last firmware for the ZenPad 10 Z300C (`WW-4.7.5.0`, Android 5.0.2) never
turns on the compressed swap (zram) it was built with. The tablet runs with
2 GB of RAM and no swap, so Android kills background apps early. This makes
the tablet feel as if apps keep crashing or restarting when you switch between them.

This repo builds a patched boot image that turns on the 512 MB zram swap ASUS
already configured. Nothing else changes: the kernel, the kernel command line
and the rest of the ramdisk stay byte-for-byte stock.

**Tested on:** Z300C (P023), firmware `WW_P023-V4.7.5-20160419`, unlocked
bootloader. After flashing, SELinux stays `Enforcing` and `/proc/swaps` lists
`/dev/block/zram0` at 524284 kB.

> [!WARNING]
> You do this at your own risk. It needs an **unlocked bootloader**. Once a
> modified boot image is installed, **don't relock the bootloader**: a locked
> bootloader checks the boot image's signature and would probably refuse to
> boot. Keep `boot-stock.img` so you can go back.

## The bug
`init.sofia3g.rc` in the stock boot ramdisk has `import /init.zram.rc`, but ASUS
never shipped that file (`init.ksm.rc` and `init.storage.rc` are missing as
well). Everything else needed is already there:

- the kernel has zram built in (`CONFIG_ZRAM=y`),
- `/fstab.zram` defines a 512 MB zram device,
- the stock `init` supports `swapon_all`,
- there's even an `init.smallram.rc` with the right settings that nothing imports.

The fix adds the missing `/init.zram.rc`, with the same settings as
`init.smallram.rc`:

```
on boot
    write /proc/sys/vm/page-cluster 0
    write /proc/sys/vm/swappiness 160
    write /sys/fs/cgroup/memory/sw/memory.swappiness 160

on post-fs-data
    swapon_all /fstab.zram
```

Details are in [docs/technical-notes.md](docs/technical-notes.md).

## What you need
- A Linux PC with Python 3.8+, `adb` and `fastboot` (e.g. the Ubuntu packages
  `adb` and `fastboot`). The standard fastboot 35 worked.
- The official firmware package `UL-P023-WW-4.7.5.0-user.zip` from ASUS,
  unzipped. It isn't included here: the boot image contains ASUS's files, so
  you build the images yourself from your own copy.
- An unlocked bootloader (see [Unlocking](#unlocking)).

## Build
```bash
unzip UL-P023-WW-4.7.5.0-user.zip -d UL-P023-WW-4.7.5.0-user
python3 build_boot.py UL-P023-WW-4.7.5.0-user out
```

The script refuses firmware other than 4.7.5.0. Before writing anything, it
also checks that repacking the stock ramdisk gives the stock image byte for
byte. The output is reproducible; you should get:

```
boot-stock.img           sha1 00b89ed3166321f7a7f1ca1076af0b18b6b973b9
boot-zram.img            sha1 df948e613b4ee945e12a27f1a375abfc287b8581
boot-zram-install.img    sha1 68d47588d5b466605ecbd639358a087b4d9806d6
boot-zram-uninstall.img  sha1 c8dd9d096cfada0ddfeaabc36a08f34007dababe
```

## Fastboot on Linux
In fastboot mode the tablet shows up as Intel `8087:0912`, not as an ASUS
device. Ubuntu's Android udev rules don't cover that ID, so fastboot reports
`no permissions (missing udev rules?)`. To fix it, install the rule from this repo:

```bash
sudo cp udev/51-z300c-fastboot.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug and replug the tablet.

## Install
1. Turn on USB debugging (Settings → About → Software information → tap
   *Build number* 7 times, then Developer options → USB debugging). Then check
   that `adb devices` lists the tablet as `device`.
2. `adb reboot bootloader`
3. **Try the image first without installing it.** This boots it once from RAM:
   ```bash
   fastboot boot out/boot-zram.img
   ```
   Once Android is up, `adb shell cat /proc/swaps` should list `/dev/block/zram0`.
   If adb doesn't see the tablet after this boot, run `adb kill-server` and
   try again.
4. Install it:
   ```bash
   adb reboot bootloader
   fastboot flash boot out/boot-zram.img
   ```
   The bootloader may answer `FAILED (remote: ' Fail to flash partition: boot')`.
   On the tested tablet, the image **was written anyway** (see
   [technical notes](docs/technical-notes.md#flashing)).
5. `fastboot reboot`, then check:
   ```bash
   adb shell ls -l /init.zram.rc     # present: the patched image is installed
   adb shell getenforce              # Enforcing
   adb shell cat /proc/swaps         # /dev/block/zram0  partition  524284 ...
   ```

## Going back to stock
```bash
adb reboot bootloader
fastboot flash boot out/boot-stock.img
fastboot reboot
```

Afterwards, `adb shell ls /init.zram.rc` should say *No such file*.
As a last resort, reinstalling the official `UL-P023-WW-4.7.5.0-user.zip`
from recovery restores the whole system.

## Fallback: hook in /system (if flashing doesn't stick)
If your bootloader really refuses to write the boot partition, zram can be
turned on from `/system` instead. The bootloader doesn't verify `/system`.
At every boot, the stock firmware runs `/system/bin/adbon.sh` as root, in an
SELinux domain that may call `swapon`.

- `boot-zram-install.img`, booted once with `fastboot boot`, copies
  `/system/etc/zram.sh` onto the tablet and adds 3 lines to `adbon.sh` that run it.
  This one boot uses `androidboot.selinux=permissive`, because the stock policy
  doesn't let even init write to `/system`. Reboot normally afterwards.
- `boot-zram-uninstall.img`, booted the same way, puts the stock `adbon.sh`
  back and deletes `zram.sh`.
- Check with `adb shell getprop zramhook.installer` (or `zramhook.uninstaller`).
  It should print `done`.

This path is built and checked, but **it hasn't been run on a tablet yet**.
Before using it, `adb shell md5 /system/bin/adbon.sh` must print
`2fc5623120fa1a75f7469077023255c1`.

## Unlocking
The 4.7.5 bootloader supports `fastboot oem unlock` and then
`fastboot oem unlock confirm`. It may ask for ASUS's modified fastboot
(V1.0.5), and it may tell you to run `fastboot -w`, which **erases all user
data**. The tested tablet was already unlocked, so this repo hasn't verified
the unlock step.

## Repo layout
| Path | What |
|---|---|
| `build_boot.py` | Unpacks the stock boot image, adds files to the ramdisk and repacks it. Standard library only. |
| `ramdisk/boot-zram/` | The missing `init.zram.rc`. |
| `ramdisk/install/` | Installer for the `/system` hook (`zram.sh` plus its `init.zram.rc`). The hooked `adbon.sh` is generated from your firmware at build time. |
| `ramdisk/uninstall/` | Uninstaller for the `/system` hook. |
| `udev/` | udev rule for fastboot mode. |
| `docs/technical-notes.md` | How the bug, the bootloader and the SELinux policy were analysed. |

## Licence
MIT for the scripts and docs in this repo; see [LICENSE](LICENSE). The firmware
and anything built from it belong to ASUS and aren't included.
