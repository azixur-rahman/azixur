#!/usr/bin/env python3
"""Apply azixur kernel configuration.

Usage: config_tweaks.py [bakasu|ksun]

Common: GPS/FM, zram/LZ4, deadline I/O scheduler, kprobes, tmpfs xattr.
bakasu: BakaSU KernelSU (with SUSFS symbols already in the defconfig).
ksun:   KernelSU-Next legacy, syscall-table hook (4.9 has no usable kprobes
        hook mode).
"""

import pathlib
import sys

DEFCONFIG = pathlib.Path("arch/arm64/configs/CPH2185_defconfig")
FLAVOR = (sys.argv[1] if len(sys.argv) > 1 else "ksun").lower()


def set_symbol(text: str, name: str, value: str) -> str:
    out = []
    replaced = False
    for line in text.splitlines():
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


def disable_symbol(text: str, name: str) -> str:
    text = remove_symbol(text, name)
    return text + f"# {name} is not set\n"


def remove_prefix(text: str, prefix: str) -> str:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix) or stripped.startswith(f"# {prefix}"):
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def main() -> int:
    if FLAVOR not in ("bakasu", "ksun"):
        print(f"ERROR: unknown flavor {FLAVOR!r}")
        return 1

    text = DEFCONFIG.read_text()

    # Common: kprobes/tmpfs xattrs are required by both root solutions.
    text = set_symbol(text, "CONFIG_KPROBES", "y")
    text = set_symbol(text, "CONFIG_KSU", "y")
    text = disable_symbol(text, "CONFIG_KSU_DEBUG")
    text = set_symbol(text, "CONFIG_TMPFS_XATTR", "y")
    text = set_symbol(text, "CONFIG_KALLSYMS_ALL", "y")

    # RAM compression and light I/O scheduler.
    text = set_symbol(text, "CONFIG_ZRAM", "y")
    text = set_symbol(text, "CONFIG_ZSMALLOC", "y")
    text = set_symbol(text, "CONFIG_CRYPTO_LZ4", "y")
    text = set_symbol(text, "CONFIG_CRYPTO_LZ4HC", "y")
    text = set_symbol(text, "CONFIG_DEFAULT_IOSCHED", '"deadline"')

    # Working connectivity selection.
    text = set_symbol(text, "CONFIG_MTK_COMBO_GPS", "y")
    text = set_symbol(text, "CONFIG_MTK_FMRADIO", "y")
    text = set_symbol(text, "CONFIG_MTK_FM_CHIP", '"MT6631_FM"')
    text = set_symbol(text, "CONFIG_MTK_GPS", "y")

    # Keep the stock "+" suffix so the module vermagic matches.
    text = set_symbol(text, "CONFIG_LOCALVERSION", '"+"')
    text = set_symbol(text, "CONFIG_LOCALVERSION_AUTO", "n")

    if FLAVOR == "bakasu":
        # BakaSU ships its own hook implementation and SUSFS. The defconfig
        # already carries the matching symbols from the previous build.
        text = set_symbol(text, "CONFIG_KSU_SUSFS", "y")
        text = set_symbol(text, "CONFIG_KSU_SUSFS_SUS_PATH", "y")
        text = disable_symbol(text, "CONFIG_KSU_SYSCALL_TABLE_HOOK")
    else:
        # KernelSU-Next legacy: kprobes hook is not usable on 4.9, use the
        # syscall table patching hook instead.
        text = set_symbol(text, "CONFIG_KSU_SYSCALL_TABLE_HOOK", "y")
        text = disable_symbol(text, "CONFIG_KSU_KPROBES_HOOK")
        text = disable_symbol(text, "CONFIG_KSU_MANUAL_HOOK")
        text = remove_symbol(text, "CONFIG_KSU_SUSFS")
        text = remove_prefix(text, "CONFIG_KSU_SUSFS_")
        text = remove_symbol(text, "CONFIG_KSU_WITH_KPROBES")

    DEFCONFIG.write_text(text)

    required = [
        "CONFIG_KSU=y",
        "CONFIG_KPROBES=y",
        "CONFIG_TMPFS_XATTR=y",
        "CONFIG_KALLSYMS_ALL=y",
        "CONFIG_ZRAM=y",
        "CONFIG_CRYPTO_LZ4=y",
        "CONFIG_MTK_COMBO_GPS=y",
        "CONFIG_MTK_FMRADIO=y",
        'CONFIG_MTK_FM_CHIP="MT6631_FM"',
    ]
    if FLAVOR == "ksun":
        required.append("CONFIG_KSU_SYSCALL_TABLE_HOOK=y")

    final = DEFCONFIG.read_text()
    for line in required:
        if line not in final:
            print(f"ERROR: missing config {line}")
            return 1
    print(f"kernel config tweaks applied for {FLAVOR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
