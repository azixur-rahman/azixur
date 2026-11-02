#!/usr/bin/env python3
"""Adapt KernelSU-Next's syscall-table hook to the 4.9 arm64 syscall ABI.

KernelSU-Next's syscall table hook is written for the pt_regs-based syscall
ABI that appeared in 4.17.  This vendor 4.9 kernel still uses the classic
plain-argument ABI: sys_call_table[__NR_execve] is sys_execve(const char __user
*, ...), not __arm64_sys_execve(const struct pt_regs *).

Keep the upstream implementation for 4.17+ and add plain-argument wrappers for
4.9.  The wrappers make a scratch copy of the live syscall pt_regs so the
existing KSU handlers (sucompat, adb root, stat/faccessat hiding) can keep
using their normal PT_REGS_* accessors, then call the original syscall with
the possibly rewritten local arguments.
"""

import pathlib
import sys

PATH = pathlib.Path("KernelSU-Next/kernel/hook/syscall_table_hook.c")
SYMBOL_RESOLVER = pathlib.Path("KernelSU-Next/kernel/infra/symbol_resolver.c")
KERNEL_COMPAT = pathlib.Path("KernelSU-Next/kernel/compat/kernel_compat.c")
NAMESPACE = pathlib.Path("fs/namespace.c")
INTERNAL_H = pathlib.Path("fs/internal.h")

PATH_UMOUNT_CODE = """/*
 * KernelSU-Next backport for 4.9.  Its Kbuild normally appends these while
 * compiling drivers/kernelsu/, which is after fs/namespace.c has already been
 * compiled; insert them before the build instead.
 */
static int can_umount(const struct path *path, int flags)
{
	struct mount *mnt = real_mount(path->mnt);

	if (flags & ~(MNT_FORCE | MNT_DETACH | MNT_EXPIRE | UMOUNT_NOFOLLOW))
		return -EINVAL;
	if (!may_mount())
		return -EPERM;
	if (path->dentry != path->mnt->mnt_root)
		return -EINVAL;
	if (!check_mnt(mnt))
		return -EINVAL;
	if (mnt->mnt.mnt_flags & MNT_LOCKED)
		return -EINVAL;
	if (flags & MNT_FORCE && !capable(CAP_SYS_ADMIN))
		return -EPERM;
	return 0;
}

int path_umount(struct path *path, int flags)
{
	struct mount *mnt = real_mount(path->mnt);
	int ret;

	ret = can_umount(path, flags);
	if (!ret)
		ret = do_umount(mnt, flags);
	dput(path->dentry);
	mntput_no_expire(mnt);
	return ret;
}

"""

NAMESPACE_ANCHOR = "static bool is_mnt_ns_file(struct dentry *dentry)\n"
INTERNAL_ANCHOR = "extern void __init mnt_init(void);\n"

COMPAT_SHIMS = """
/* azixur_4_9_shims: symbols expected by this vendor 4.9 kernel */
#include <linux/jump_label.h>

struct static_key_true ksu_is_init_rc_hook_enabled = STATIC_KEY_TRUE_INIT;

/*
 * fs/exec.c calls the post-execveat hook unconditionally when CONFIG_KSU is
 * set.  KernelSU-Next only implements the pre-execveat handler; SUSFS owns
 * the post hook on this tree, so a no-op is correct for the ksun flavor.
 */
int ksu_handle_post_execveat(int *fd, struct filename **filename_ptr,
			     void *argv, void *envp, int *flags, int *retval)
{
	return 0;
}
"""


def patch_path_umount() -> bool:
    """Insert path_umount before fs/ is compiled, not during drivers/."""
    text = NAMESPACE.read_text()
    if "int path_umount(struct path *path, int flags)" not in text:
        if NAMESPACE_ANCHOR not in text:
            print("ERROR: fs/namespace.c insertion point not found")
            return False
        text = text.replace(
            NAMESPACE_ANCHOR, PATH_UMOUNT_CODE + NAMESPACE_ANCHOR, 1
        )
        NAMESPACE.write_text(text)
        print("KernelSU-Next: path_umount inserted into fs/namespace.c")
    else:
        print("KernelSU-Next: fs/namespace.c already has path_umount")

    header = INTERNAL_H.read_text()
    if "int path_umount(struct path *path, int flags);" not in header:
        if INTERNAL_ANCHOR not in header:
            print("ERROR: fs/internal.h insertion point not found")
            return False
        header = header.replace(
            INTERNAL_ANCHOR,
            INTERNAL_ANCHOR
            + "int path_umount(struct path *path, int flags);\n",
            1,
        )
        INTERNAL_H.write_text(header)
        print("KernelSU-Next: path_umount declared in fs/internal.h")
    return True


def patch_kvmalloc() -> bool:
    """kvmalloc_array() does not exist in 4.9; kvmalloc() does via compat."""
    if not SYMBOL_RESOLVER.is_file():
        print(f"ERROR: {SYMBOL_RESOLVER} not found")
        return False
    src = SYMBOL_RESOLVER.read_text()
    old = "kvmalloc_array(count, sizeof(*ctx->entries), GFP_KERNEL)"
    new = "kvmalloc(count * sizeof(*ctx->entries), GFP_KERNEL)"
    if old in src:
        src = src.replace(old, new, 1)
        SYMBOL_RESOLVER.write_text(src)
        print("KernelSU-Next: symbol_resolver.c uses kvmalloc on 4.9")
    else:
        print("KernelSU-Next: symbol_resolver.c already uses kvmalloc")
    return True


def patch_compat_shims() -> bool:
    """Provide the two symbols the vendor fs/ hooks expect from KSU."""
    if not KERNEL_COMPAT.is_file():
        print(f"ERROR: {KERNEL_COMPAT} not found")
        return False
    src = KERNEL_COMPAT.read_text()
    if "azixur_4_9_shims" in src:
        print("KernelSU-Next: 4.9 compat shims already present")
        return True
    if "int ksu_handle_post_execveat" in src:
        print("KernelSU-Next: post_execveat already implemented upstream")
        return True
    KERNEL_COMPAT.write_text(src + COMPAT_SHIMS)
    print("KernelSU-Next: added 4.9 init_rc/post_execveat shims")
    return True

OLD_ERROR = """// The pt_regs-based syscall ABI (__arm64_sys_*/__x64_sys_*) exists since 4.17
#if LINUX_VERSION_CODE < KERNEL_VERSION(4, 17, 0)
#error "syscall table hook requires kernel >= 4.17 (pt_regs syscall ABI)"
#endif
"""

NEW_COMMENT = """// The pt_regs-based syscall ABI (__arm64_sys_*/__x64_sys_*) exists since 4.17.
// Older kernels use plain-argument syscall wrappers; a compatibility version
// is provided at the bottom of this file.
"""

DEF_ANCHOR = """#ifdef __NR_execve
static long ksu_sth_execve(const struct pt_regs *regs)"""

DEF_REPLACEMENT = """#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 17, 0)

#ifdef __NR_execve
static long ksu_sth_execve(const struct pt_regs *regs)"""

OLD_WRAPPERS = r"""#else /* LINUX_VERSION_CODE < KERNEL_VERSION(4, 17, 0) */

/*
 * 4.9 compatibility: sys_call_table[__NR_*] points at the classic
 * plain-argument sys_* functions.  Build a scratch copy of the live syscall
 * pt_regs for the KSU handlers, then invoke the original syscall with the
 * (possibly rewritten) local arguments.
 */

#ifdef __NR_execve
static long ksu_sth_execve(const char __user *filename,
			   const char __user *const __user *argv,
			   const char __user *const __user *envp)
{
	struct pt_regs fake = *current_pt_regs();
	const char __user *fn = filename;
	long ret;

	fake.regs[0] = (unsigned long)fn;
	fake.regs[1] = (unsigned long)argv;
	fake.regs[2] = (unsigned long)envp;

	if (current->pid != 1 && is_init(current_cred())) {
		ksu_sth_init_exec_tracker(fn);
		ret = ksu_adb_root_handle_execve(&fake);
		if (ret)
			pr_err("sth: adb root failed: %ld\n", ret);
		envp = (const char __user *const __user *)fake.regs[2];
	} else {
		ksu_handle_execve_sucompat(&fn, __NR_execve, &fake);
	}

	return ((long (*)(const char __user *,
			  const char __user *const __user *,
			  const char __user *const __user *))
		ksu_sth_get_orig(__NR_execve))(fn, argv, envp);
}
#endif

#ifdef __NR_execveat
static long ksu_sth_execveat(int dfd, const char __user *filename,
			     const char __user *const __user *argv,
			     const char __user *const __user *envp, int flags)
{
	struct pt_regs fake = *current_pt_regs();
	const char __user *fn = filename;
	long ret;

	fake.regs[0] = (unsigned long)dfd;
	fake.regs[1] = (unsigned long)fn;
	fake.regs[2] = (unsigned long)argv;
	fake.regs[3] = (unsigned long)envp;
	fake.regs[4] = (unsigned long)flags;

	if ((int)fake.regs[0] == AT_FDCWD && (int)fake.regs[3] == 0) {
		if (current->pid != 1 && is_init(current_cred())) {
			ksu_sth_init_exec_tracker(fn);
			ret = ksu_adb_root_handle_execveat(&fake);
			if (ret)
				pr_err("sth: adb root failed: %ld\n", ret);
			envp = (const char __user *const __user *)fake.regs[3];
		} else {
			ksu_handle_execveat_sucompat_user(&fn, __NR_execveat,
							  &fake);
		}
	}

	return ((long (*)(int, const char __user *,
			  const char __user *const __user *,
			  const char __user *const __user *, int))
		ksu_sth_get_orig(__NR_execveat))(dfd, fn, argv, envp, flags);
}
#endif

#ifdef __NR_faccessat
static long ksu_sth_faccessat(int dfd, const char __user *filename, int mode)
{
	struct pt_regs fake = *current_pt_regs();
	const char __user *fn = filename;
	int dfd_local = dfd, mode_local = mode;

	fake.regs[0] = (unsigned long)dfd_local;
	fake.regs[1] = (unsigned long)fn;
	fake.regs[2] = (unsigned long)mode_local;

	ksu_handle_faccessat(&dfd_local, &fn, &mode_local, NULL);

	return ((long (*)(int, const char __user *, int))
		ksu_sth_get_orig(__NR_faccessat))(dfd_local, fn, mode_local);
}
#endif

#ifdef __NR_newfstatat
static long ksu_sth_newfstatat(int dfd, const char __user *filename,
			       void __user *statbuf, int flags)
{
	struct pt_regs fake = *current_pt_regs();
	const char __user *fn = filename;
	int dfd_local = dfd, flags_local = flags;

	fake.regs[0] = (unsigned long)dfd_local;
	fake.regs[1] = (unsigned long)fn;
	fake.regs[2] = (unsigned long)statbuf;
	fake.regs[3] = (unsigned long)flags_local;

	ksu_handle_stat(&dfd_local, &fn, &flags_local);

	return ((long (*)(int, const char __user *, void __user *, int))
		ksu_sth_get_orig(__NR_newfstatat))(dfd_local, fn, statbuf,
						   flags_local);
}
#endif

#endif /* LINUX_VERSION_CODE < KERNEL_VERSION(4, 17, 0) */

/*
 * The hooks[] table stores upstream-style function pointers.  The 4.9
 * wrappers use a different (correct) calling convention, so cast them only
 * for storage; they are never called through that type.
 */
#if LINUX_VERSION_CODE < KERNEL_VERSION(4, 17, 0)
#define KSU_STH_CAST(fn) ((ksu_syscall_fn_t)(fn))
#else
#define KSU_STH_CAST(fn) (fn)
#endif

"""

INIT_ANCHOR = "void __init ksu_syscall_table_hook_init(void)\n{"

HOOK_ENTRIES = (
    ("{ __NR_execve, ksu_sth_execve },", "{ __NR_execve, KSU_STH_CAST(ksu_sth_execve) },"),
    ("{ __NR_execveat, ksu_sth_execveat },",
     "{ __NR_execveat, KSU_STH_CAST(ksu_sth_execveat) },"),
    ("{ __NR_faccessat, ksu_sth_faccessat },",
     "{ __NR_faccessat, KSU_STH_CAST(ksu_sth_faccessat) },"),
    ("{ __NR_newfstatat, ksu_sth_newfstatat },",
     "{ __NR_newfstatat, KSU_STH_CAST(ksu_sth_newfstatat) },"),
)


def main() -> int:
    if not PATH.is_file():
        print(f"ERROR: {PATH} not found")
        return 1

    text = PATH.read_text()

    if NEW_COMMENT.strip() in text:
        print("KernelSU-Next 4.9 syscall ABI patch already applied")
        return 0

    if OLD_ERROR not in text:
        print("ERROR: KSU syscall hook version guard not found")
        return 1
    text = text.replace(OLD_ERROR, NEW_COMMENT, 1)

    if DEF_ANCHOR not in text:
        print("ERROR: KSU syscall wrapper section not found")
        return 1
    text = text.replace(DEF_ANCHOR, DEF_REPLACEMENT, 1)

    if INIT_ANCHOR not in text:
        print("ERROR: KSU syscall hook init not found")
        return 1
    text = text.replace(INIT_ANCHOR, OLD_WRAPPERS + INIT_ANCHOR, 1)

    for old, new in HOOK_ENTRIES:
        if old not in text:
            print(f"ERROR: hook entry not found: {old}")
            return 1
        text = text.replace(old, new, 1)

    # The compatibility block relies on current_pt_regs().
    include_anchor = "#include <asm/ptrace.h>\n"
    if include_anchor not in text:
        print("ERROR: asm/ptrace.h include not found")
        return 1
    text = text.replace(
        include_anchor,
        include_anchor
        + "#include <linux/ptrace.h>\n"
        + "#include <asm/processor.h>\n",
        1,
    )

    PATH.write_text(text)
    print("KernelSU-Next syscall table hook adapted to 4.9 arm64 ABI")

    # linux/sched/signal.h appeared in 4.11.  symbol_resolver.c includes it
    # unconditionally, so fold it into linux/sched.h on this 4.9 kernel.
    if SYMBOL_RESOLVER.is_file():
        src = SYMBOL_RESOLVER.read_text()
        if "#include <linux/sched/signal.h>" in src:
            src = src.replace(
                "#include <linux/sched/signal.h>",
                "#include <linux/sched.h>",
                1,
            )
            SYMBOL_RESOLVER.write_text(src)
            print("KernelSU-Next symbol_resolver.c adapted to 4.9 headers")
        else:
            print("KernelSU-Next symbol_resolver.c already adapted")
    else:
        print(f"ERROR: {SYMBOL_RESOLVER} not found")
        return 1

    if not patch_path_umount():
        return 1
    if not patch_kvmalloc():
        return 1
    if not patch_compat_shims():
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
