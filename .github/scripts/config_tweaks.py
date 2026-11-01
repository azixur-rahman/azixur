#!/usr/bin/env python3
"""Apply the azixur kernel configuration: KernelSU-Next, kprobes, zram and
light performance defaults. Keeps the known-working touch/vendor options."""

import pathlib
import re
import sys

DEFCONFIG = pathlib.Path("arch/arm64/configs/CPH2185_defconfig")


def set_symbol(text: str, name: str, value: str) -> str:
    lines = text.splitlines()
    out = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{name}=") or stripped == f"# {name} is not set":
            if not replaced:
                out.append(f"{name}={value}")
                replaced = True
            continue
        out.append(line)
    if not replaced:
        out.append(f"{name}={value}")
    return "\n".join(out) + "\n"


def remove_symbol(text: str, name: str) -> str:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{name}=") or stripped == f"# {name} is not set":
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def main() -> int:
    text = DEFCONFIG.read_text()

    # KernelSU-Next needs kprobes and tmpfs xattrs on 4.9.
    text = set_symbol(text, "CONFIG_KPROBES", "y")
    text = set_symbol(text, "CONFIG_KSU", "y")
    text = remove_symbol(text, "CONFIG_KSU_DEBUG")
    text += "# CONFIG_KSU_DEBUG is not set\n"
    text = set_symbol(text, "CONFIG_TMPFS_XATTR", "y")
    text = set_symbol(text, "CONFIG_KALLSYMS_ALL", "y")
    # Kernel below 5.10: patch the syscall table instead of kprobes hooks.
    text = set_symbol(text, "CONFIG_KSU_SYSCALL_TABLE_HOOK", "y")
    text = remove_symbol(text, "CONFIG_KSU_KPROBES_HOOK")
    text += "# CONFIG_KSU_KPROBES_HOOK is not set\n"
    text = remove_symbol(text, "CONFIG_KSU_MANUAL_HOOK")
    text += "# CONFIG_KSU_MANUAL_HOOK is not set\n"
    text = remove_symbol(text, "CONFIG_KSU_WITH_KPROBES")

    # RAM compression: zram is already enabled, make sure the pieces exist.
    text = set_symbol(text, "CONFIG_ZRAM", "y")
    text = set_symbol(text, "CONFIG_ZSMALLOC", "y")
    text = set_symbol(text, "CONFIG_CRYPTO_LZ4", "y")
    text = set_symbol(text, "CONFIG_CRYPTO_LZ4HC", "y")

    # Light performance default: deadline is cheaper than cfq on eMMC.
    text = set_symbol(text, "CONFIG_DEFAULT_IOSCHED", '"deadline"')

    # Keep the working connectivity selection.
    text = set_symbol(text, "CONFIG_MTK_COMBO_GPS", "y")
    text = set_symbol(text, "CONFIG_MTK_FMRADIO", "y")
    text = set_symbol(text, "CONFIG_MTK_FM_CHIP", '"MT6631_FM"')
    text = set_symbol(text, "CONFIG_MTK_GPS", "y")

    # Keep the stock "+" suffix so the module vermagic matches.
    text = set_symbol(text, "CONFIG_LOCALVERSION", '"+"')
    text = set_symbol(text, "CONFIG_LOCALVERSION_AUTO", "n")

    DEFCONFIG.write_text(text)

    required = [
        "CONFIG_KSU=y",
        "CONFIG_KPROBES=y",
        "CONFIG_TMPFS_XATTR=y",
        "CONFIG_ZRAM=y",
        "CONFIG_CRYPTO_LZ4=y",
        "CONFIG_MTK_COMBO_GPS=y",
        "CONFIG_MTK_FMRADIO=y",
        'CONFIG_MTK_FM_CHIP="MT6631_FM"',
    ]
    for line in required:
        if line not in DEFCONFIG.read_text():
            print(f"ERROR: missing config {line}")
            return 1
    if "CONFIG_KSU_WITH_KPROBES" in DEFCONFIG.read_text():
        print("ERROR: stale BakaSU config still present")
        return 1
    print("kernel config tweaks applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
