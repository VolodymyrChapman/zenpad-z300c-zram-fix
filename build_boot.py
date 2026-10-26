#!/usr/bin/env python3
"""Build ASUS ZenPad 10 (Z300C) boot images for the zram fix.

Takes the stock boot image out of the firmware's fwu_image.bin, adds the
files of each ramdisk/<variant> directory to the root of its ramdisk and
repacks it. The kernel and the second-stage blob are kept byte for byte.

Usage: python3 build_boot.py <firmware_dir> <outdir>
<firmware_dir> is the unzipped UL-P023-WW-4.7.5.0-user.zip; it must contain
fwu_image.bin and system/bin/adbon.sh.

Writes to <outdir>:
  boot-stock.img            the stock boot image, as a backup
  boot-zram.img             ramdisk/boot-zram: turns zram on at every boot
  boot-zram-install.img     ramdisk/install: adds the zram hook to /system
  boot-zram-uninstall.img   ramdisk/uninstall: puts the stock adbon.sh back
"""
import gzip
import hashlib
import os
import struct
import sys

BOOT_MAGIC = b"ANDROID!"
HERE = os.path.dirname(os.path.abspath(__file__))

# Firmware WW-4.7.5.0, the last release for the Z300C. Other versions have
# a different ramdisk and haven't been checked, so the build refuses them.
STOCK_BOOT_SHA1 = "00b89ed3166321f7a7f1ca1076af0b18b6b973b9"
STOCK_ADBON_MD5 = "2fc5623120fa1a75f7469077023255c1"

# The installer and uninstaller write to /system, which the stock SELinux
# policy forbids even for init; the stock init honours this option.
PERMISSIVE = b"androidboot.selinux=permissive"

# output name: (ramdisk/ variant, text appended to the kernel command line)
VARIANTS = {
    "boot-zram.img": ("boot-zram", b""),
    "boot-zram-install.img": ("install", PERMISSIVE),
    "boot-zram-uninstall.img": ("uninstall", PERMISSIVE),
}

# Inserted after the #! line of the stock /system/bin/adbon.sh, which init
# runs as root (SELinux domain init_shell) on every boot
ADBON_HOOK = (b"# zram hook (z300c zram fix): turn on the 512 MB zram swap that the\n"
              b"# stock boot image leaves off. Undo with boot-zram-uninstall.img.\n"
              b"/system/bin/sh /system/etc/zram.sh\n")


def pad(n, page):
    return (n + page - 1) // page * page


def find_boot(fw):
    # fwu_image.bin holds two Android images: boot first, then recovery.
    off = fw.find(BOOT_MAGIC)
    if off < 0:
        sys.exit("no boot image found in firmware")
    return off


def parse(img):
    ks, ka, rs, ra, ss, sa, ta, ps = struct.unpack_from("<8I", img, 8)
    kernel = img[ps:ps + ks]
    r0 = ps + pad(ks, ps)
    ramdisk = img[r0:r0 + rs]
    s0 = r0 + pad(rs, ps)
    second = img[s0:s0 + ss]
    return dict(ka=ka, ra=ra, sa=sa, ta=ta, ps=ps, header=img[:ps],
                kernel=kernel, ramdisk=ramdisk, second=second,
                size=s0 + pad(ss, ps))


def cpio_entries(data):
    """Yield (header_fields, name, body) for each newc cpio entry."""
    i = 0
    while True:
        if data[i:i + 6] != b"070701":
            sys.exit("unexpected cpio format at offset %d" % i)
        f = [int(data[i + 6 + 8 * k:i + 14 + 8 * k], 16) for k in range(13)]
        namesize, filesize = f[11], f[6]
        name_end = i + 110 + namesize
        name = data[i + 110:name_end - 1]
        body_start = pad(name_end, 4)
        body = data[body_start:body_start + filesize]
        yield f, name, body
        if name == b"TRAILER!!!":
            return
        i = pad(body_start + filesize, 4)


def cpio_entry(f, name, body):
    f = list(f)
    f[6] = len(body)
    f[11] = len(name) + 1
    hdr = b"070701" + b"".join(b"%08X" % v for v in f) + name + b"\0"
    hdr += b"\0" * (pad(len(hdr), 4) - len(hdr))
    return hdr + body + b"\0" * (pad(len(body), 4) - len(body))


def add_files(cpio, files):
    """Add {name: (body, mode)} to the root of a newc cpio archive."""
    out, ino = [], 0
    for f, n, b in cpio_entries(cpio):
        if n in files:
            sys.exit("%s already present in ramdisk" % n.decode())
        if n == b"TRAILER!!!":
            for name, (body, mode) in sorted(files.items()):
                ino += 1
                # uid 0, gid 0, nlink 1, mtime 0, same layout as the build system
                new = [ino, mode, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
                out.append(cpio_entry(new, name, body))
        ino = max(ino, f[0])
        out.append(cpio_entry(f, n, b))
    blob = b"".join(out)
    return blob + b"\0" * (pad(len(blob), 512) - len(blob))


def variant_files(variant, adbon):
    files = {}
    vdir = os.path.join(HERE, "ramdisk", variant)
    for name in sorted(os.listdir(vdir)):
        # init only reads .rc files that aren't group/world writable
        mode = 0o100750 if name.endswith(".rc") else 0o100644
        body = open(os.path.join(vdir, name), "rb").read()
        files[name.encode()] = (body, mode)
    # adbon.sh is ASUS's, so it's taken from the firmware rather than kept here
    if variant == "install":
        shebang, rest = adbon.split(b"\n", 1)
        files[b"adbon.sh"] = (shebang + b"\n" + ADBON_HOOK + rest, 0o100644)
    elif variant == "uninstall":
        files[b"adbon.sh"] = (adbon, 0o100644)
    return files


def build(parts, ramdisk, cmdline_extra=b""):
    ps = parts["ps"]
    sha = hashlib.sha1()
    for blob in (parts["kernel"], ramdisk, parts["second"]):
        sha.update(blob)
        sha.update(struct.pack("<I", len(blob)))
    hdr = bytearray(parts["header"])
    struct.pack_into("<I", hdr, 16, len(ramdisk))
    if cmdline_extra:
        cmdline = bytes(hdr[64:576]).rstrip(b"\0") + b" " + cmdline_extra
        if len(cmdline) >= 512:
            sys.exit("kernel command line too long")
        hdr[64:576] = cmdline.ljust(512, b"\0")
    hdr[576:608] = sha.digest().ljust(32, b"\0")

    def page(b):
        return b + b"\0" * (pad(len(b), ps) - len(b))
    return bytes(hdr) + page(parts["kernel"]) + page(ramdisk) + page(parts["second"])


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    fwdir, outdir = sys.argv[1], sys.argv[2]
    fw = open(os.path.join(fwdir, "fwu_image.bin"), "rb").read()
    adbon = open(os.path.join(fwdir, "system", "bin", "adbon.sh"), "rb").read()

    off = find_boot(fw)
    parts = parse(fw[off:])
    stock = fw[off:off + parts["size"]]
    if hashlib.sha1(stock).hexdigest() != STOCK_BOOT_SHA1:
        sys.exit("boot image isn't the one from firmware WW-4.7.5.0; not building")
    if hashlib.md5(adbon).hexdigest() != STOCK_ADBON_MD5:
        sys.exit("system/bin/adbon.sh isn't the one from firmware WW-4.7.5.0; not building")

    # Sanity check: rebuilding with the stock ramdisk must give the stock image
    if build(parts, parts["ramdisk"]) != stock:
        sys.exit("repacker does not reproduce the stock image; not writing output")

    cpio = gzip.decompress(parts["ramdisk"])
    images = {"boot-stock.img": stock}
    for out_name, (variant, cmdline_extra) in VARIANTS.items():
        files = variant_files(variant, adbon)
        ramdisk = gzip.compress(add_files(cpio, files), 9, mtime=0)
        images[out_name] = build(parts, ramdisk, cmdline_extra)

    os.makedirs(outdir, exist_ok=True)

    for out_name, image in images.items():
        open(os.path.join(outdir, out_name), "wb").write(image)
        print("%-24s %d bytes, sha1 %s" % (out_name, len(image), hashlib.sha1(image).hexdigest()))


if __name__ == "__main__":
    main()
