# Technical notes

Device: ASUS ZenPad 10 Z300C (P023). Intel SoFIA 3G-R (Atom x3-C3200, x86),
2 GB RAM, Android 5.0.2 `LRX22G`, firmware `WW_P023-V4.7.5-20160419`, kernel 3.10
(`i386_sofia_defconfig`, DTS `SF_3GR-es1-sr-garnet-Z300C.dts`).

## Firmware layout
`UL-P023-WW-4.7.5.0-user.zip` is an OTA package. `updater-script` writes
`fwu_image.bin` to `ImcPartID122` and applies it with
`intel_install_firmware_update`. `fwu_image.bin` is an Intel firmware update
container. It holds two standard Android boot images (`ANDROID!` magic,
page size 4096):

| Offset in `fwu_image.bin` | Image | Command line |
|---|---|---|
| 15024640 | boot | `idle=halt androidboot.hardware=sofia3g ... notsc` |
| 23298560 | recovery | the same, plus `androidboot.selinux=permissive` |

Each image has a 6 MB gzip bzImage, a gzip newc cpio ramdisk and a 221 KB
"second" blob. The `build_boot.py` repacker keeps the kernel, the second blob
and all other header fields. It recomputes the SHA-1 id the same way
`mkbootimg` does, and checks that repacking the stock ramdisk reproduces the
stock image byte for byte.

## Memory configuration found in the stock ramdisk
- `init.sofia3g.rc` imports `/init.ksm.rc`, `/init.zram.rc` and `/init.storage.rc`,
  none of which exist.
- `fstab.zram`: `/dev/block/zram0 none swap defaults zramsize=536870912`
- `init.smallram.rc` (page-cluster 0, swappiness 160, `swapon_all /fstab.zram`)
  is never imported.
- After boot completes, `init.sofia3g.rc` sets `vm.extra_free_kbytes=17000`
  and `read_ahead_kb` 128.
- Swappiness 160 is valid on this kernel: Intel commented out the 100 limit
  in `kernel/sysctl.c`. The memcg limit (`mm/memcontrol.c`) is 200.
- The DTS reserves about 176 MB (CMA 80 MB plus 96 MB of secure regions),
  leaving `MemTotal` at about 1.94 GB.

## Bootloader (Intel SLB)
The strings in `fwu_image.bin` show the fastboot implementation:

- `oem unlock` / `oem unlock confirm`, `getvar:unlocked`, `Device is already unlocked!`
- `getvar:check-fastboot-version` and `INVALID FASTBOOT VERSION, PLEASE USE FASTBOOT V1.0.5`
  (ASUS's modified fastboot). Standard fastboot 35 could still `boot` and `flash`
  on an unlocked tablet.
- Partition verification: `Load partition with secpack`, `Validate partition HASH`,
  `VRL need to be flashed!`, `VRL verify fail`, `Fail to flash partition: %s`.

### Flashing
`fastboot boot <unsigned image>` works when unlocked.

`fastboot flash boot out/boot-zram.img` printed:

```
(bootloader)  boot partition size: 0x0000000001100000
Sending 'boot' (8072 KB)    OKAY
Writing 'boot'              (bootloader)  Flashing partition: boot
FAILED (remote: ' Fail to flash partition: boot')
```

Yet after a plain reboot, with no fastboot step in between (confirmed in the
host's USB log: Android → Intel boot ROM `8087:07f6` → Android), the tablet
ran the patched ramdisk. So the data was written, and the failure is reported
by a later step, most likely the signature (VRL/secpack) update. An unlocked
SLB boots the image anyway. A locked one presumably would not.

## USB IDs
| ID | Mode |
|---|---|
| `0b05:7773` | Android, MTP + ADB |
| `0b05:5d0f` | Android, MTP only (`sys.usb.config=mtp`) |
| `8087:0912` | Fastboot (SLB). Not in Ubuntu's `51-android.rules`, hence `udev/51-z300c-fastboot.rules`. |
| `8087:07f6` | Intel boot ROM download mode. Shows up for about 4 s on every power-on. |

After `fastboot boot`, the host's adb server can lose the device when adbd
restarts early in boot (`usb read failed: status = 5`). Running `adb kill-server`
fixes this.

## SELinux analysis (for the /system hook)
The policy is `/sepolicy` from the stock ramdisk, read with setools 4.4:

- `type_transition init shell_exec:process init_shell` means services that
  init runs via `/system/bin/sh` run as `init_shell`.
- `init_shell` has the attribute `unconfineddomain`, with `capability sys_admin`
  and `sys_module`.
- `/dev/block/zram0` is labelled `nvm_block_device`. `init_shell` may
  open, read and write it.
- `unconfineddomain` may write `proc` and `sysfs` files. `domain` may write
  `cgroup` files.
- `sys_swapon` in this kernel only checks `CAP_SYS_ADMIN`
  (`mm/swapfile.c:1830`); there's no separate LSM hook for it.
- **No** domain, init included, may write `system_file`. So the installer can only
  run with SELinux permissive.
- The stock `init` binary reads `ro.boot.selinux` and calls
  `security_setenforce(0)` for `permissive` (around `0x8049c35`). In other
  words, AOSP's `ALLOW_DISABLE_SELINUX` path is compiled into this user build.

The stock services that run `/system/*.sh` scripts as root at every boot are
`enable_adb` (`/system/bin/adbon.sh`) and `load_iwlwifi`
(`/system/bin/load_iwlwifi.sh`). The hook uses `adbon.sh`, because breaking
it can only affect USB mode switching, not Wi-Fi.

`/system` has no `verify` flag in `fstab.sofia3g`, so dm-verity is off.
