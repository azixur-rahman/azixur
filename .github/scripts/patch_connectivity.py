#!/usr/bin/env python3
"""Enable GPS + FM radio as built-in drivers and make their makefiles use
the in-tree connectivity sources instead of the vendor symlink tree."""

import pathlib
import re
import sys

CONN = pathlib.Path("drivers/misc/mediatek/connectivity")


def read(path: pathlib.Path) -> str:
    return path.read_text()


def write(path: pathlib.Path, text: str) -> None:
    path.write_text(text)


def strip_werror(text: str) -> str:
    text = re.sub(r"(?<!\S)-Werror(?!\S)", "", text)
    text = text.replace("KBUILD_MODPOST_FAIL_ON_WARNINGS := y",
                        "KBUILD_MODPOST_FAIL_ON_WARNINGS := n")
    text = text.replace("KBUILD_MODPOST_FAIL_ON_WARNINGS=y",
                        "KBUILD_MODPOST_FAIL_ON_WARNINGS=n")
    return text


def main() -> int:
    top = CONN / "Makefile"
    text = read(top)
    text = re.sub(r"^(\s*)#\s*(obj-\$\(CONFIG_MTK_COMBO_GPS\) \+= gps/)",
                  r"\1\2", text, flags=re.M)
    text = re.sub(r"^(\s*)#\s*(obj-\$\(CONFIG_MTK_FMRADIO\) \+= fmradio/)",
                  r"\1\2", text, flags=re.M)
    text = strip_werror(text)
    write(top, text)

    for entry in ("obj-$(CONFIG_MTK_COMBO_GPS) += gps/",
                  "obj-$(CONFIG_MTK_FMRADIO) += fmradio/"):
        if entry not in read(top):
            print(f"ERROR: {entry} missing from connectivity/Makefile")
            return 1

    # GPS driver: built-in instead of module, in-tree WMT sources.
    gps = CONN / "gps" / "Makefile"
    text = read(gps)
    text = text.replace(
        "$(TOP)/vendor/mediatek/kernel_modules/connectivity/common",
        "$(srctree)/drivers/misc/mediatek/connectivity/common")
    text = text.replace("obj-m +=", "obj-y +=")
    text = text.replace("-objs", "-y")
    text = strip_werror(text)
    write(gps, text)
    if "obj-y += $(MODULE_NAME).o" not in read(gps):
        print("ERROR: GPS driver is not built in")
        return 1
    if "obj-m" in read(gps):
        print("ERROR: GPS driver still has obj-m")
        return 1
    if "$(TOP)/vendor" in read(gps):
        print("ERROR: GPS driver still uses vendor path")
        return 1

    # FM radio: built-in instead of module, in-tree WMT sources. The chip
    # make variables use -objs, so converting every -objs is intentional.
    fm = CONN / "fmradio" / "Makefile"
    text = read(fm)
    text = text.replace(
        "$(TOP)/vendor/mediatek/kernel_modules/connectivity/common",
        "$(srctree)/drivers/misc/mediatek/connectivity/common")
    text = text.replace(
        "$(TOP)/vendor/mediatek/kernel_modules/connectivity/conninfra",
        "$(srctree)/drivers/misc/mediatek/connectivity/conninfra")
    text = text.replace("obj-m +=", "obj-y +=")
    text = text.replace("-objs", "-y")
    text = strip_werror(text)
    write(fm, text)
    if "obj-y += $(MODULE_NAME).o" not in read(fm):
        print("ERROR: FM radio driver is not built in")
        return 1
    if "obj-m" in read(fm):
        print("ERROR: FM radio driver still has obj-m")
        return 1
    if "$(TOP)/vendor" in read(fm):
        print("ERROR: FM radio driver still uses vendor path")
        return 1

    print("GPS/FM built-in makefile patches applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
