### CPH2185 AnyKernel installer
# Author: Azizur Rahman <iremm12rr@gmail.com>
# This device uses a standard Android boot image, but some recoveries cannot
# run the bundled magiskboot parser. The installer keeps the stock boot image
# and replaces its kernel, moving later boot-image components when necessary.
properties() { '
kernel.string=Falcon-AZ Kernel by Azizur Rahman
do.devicecheck=0
do.modules=0
do.systemless=0
do.cleanup=1
do.cleanuponabort=0
device.name1=CPH2185
device.name2=CPH2179
supported.versions=
supported.patchlevels=
supported.vendorpatchlevels=
'; }

BLOCK=auto;
IS_SLOT_DEVICE=auto;
PATCH_VBMETA_FLAG=auto;

. tools/ak3-core.sh;

read_u32() {
  od -An -tu4 -j "$2" -N 4 "$1" | tr -d '[:space:]';
}

align_to_page() {
  echo $((($1 + $2 - 1) / $2 * $2));
}

write_u32_le() {
  local file offset value;
  file=$1;
  offset=$2;
  value=$3;
  printf '%b' "\\$(printf '%03o' $((value & 255)))\\$(printf '%03o' $(((value >> 8) & 255)))\\$(printf '%03o' $(((value >> 16) & 255)))\\$(printf '%03o' $(((value >> 24) & 255)))" > "$AKHOME/u32.bin";
  dd if="$AKHOME/u32.bin" of="$file" bs=1 seek="$offset" count=4 conv=notrunc 2>/dev/null;
}

copy_pages() {
  local name offset size;
  name=$1;
  offset=$2;
  size=$3;
  [ "$size" -gt 0 ] || return 0;
  dd if="$BOOTIMG" of="$AKHOME/$name" bs="$BOOT_PAGE" skip=$((offset / BOOT_PAGE)) count=$((size / BOOT_PAGE)) 2>/dev/null || abort "Boot component backup failed. Aborting...";
}

write_pages() {
  local name offset size;
  name=$1;
  offset=$2;
  size=$3;
  [ "$size" -gt 0 ] || return 0;
  dd if="$AKHOME/$name" of="$AKHOME/boot-new.img" bs="$BOOT_PAGE" seek=$((offset / BOOT_PAGE)) count=$((size / BOOT_PAGE)) conv=notrunc 2>/dev/null || abort "Boot component restore failed. Aborting...";
}

zero_pages() {
  local start end;
  start=$1;
  end=$2;
  [ "$end" -gt "$start" ] || return 0;
  dd if=/dev/zero of="$AKHOME/boot-new.img" bs="$BOOT_PAGE" seek=$((start / BOOT_PAGE)) count=$(((end - start) / BOOT_PAGE)) conv=notrunc 2>/dev/null || abort "Boot image cleanup failed. Aborting...";
}

# Return absolute byte offsets of every FDT magic in the tail of a file.
# MTK appends its DTB unaligned, so this must scan bytes, not 32-bit words.
find_fdt_offsets() {
  local file size start window;
  file=$1;
  size=$(wc -c < "$file");
  window=1048576;
  start=0;
  [ "$size" -gt "$window" ] && start=$((size - window));
  "$BIN/busybox" od -A n -t x1 -v -w16 -j "$start" -N "$window" "$file" 2>/dev/null \
    | "$BIN/busybox" awk -v base="$start" '
      {
        for (i = 1; i <= NF; i++) {
          if (p3 == "d0" && p2 == "0d" && p1 == "fe" && $i == "ed")
            print base + n + i - 4;
          p3 = p2; p2 = p1; p1 = $i;
        }
        n += NF;
      }
    ';
}

read_be32() {
  local file offset b1 b2 b3 b4;
  file=$1;
  offset=$2;
  dd if="$file" of="$AKHOME/be32.bin" bs=1 skip="$offset" count=4 2>/dev/null || return 1;
  set -- $(od -A n -t u1 "$AKHOME/be32.bin" 2>/dev/null);
  [ $# -eq 4 ] || return 1;
  b1=$1; b2=$2; b3=$3; b4=$4;
  echo $((b1 * 16777216 + b2 * 65536 + b3 * 256 + b4));
}

# The stock boot image carries an OPPO-specific DTB appended to the kernel.
# Replacing it with the generic mt6765 DTB loses OPPO nodes (fingerprint,
# secure boot, project data), so keep the stock DTB whenever possible.
extract_stock_dtb() {
  local old_size dtb_section_size file_pages off total off_struct off_strings version;
  local best_off best_total;
  old_size=$1;
  dtb_section_size=$2;
  [ "$dtb_section_size" -eq 0 ] || return 1;

  file_pages=$(($(align_to_page "$old_size" "$BOOT_PAGE") / BOOT_PAGE));
  dd if="$BOOTIMG" of="$AKHOME/old-kernel.bin" bs="$BOOT_PAGE" skip=1 count="$file_pages" 2>/dev/null || return 1;

  best_off=;
  best_total=;
  for off in $(find_fdt_offsets "$AKHOME/old-kernel.bin"); do
    total=$(read_be32 "$AKHOME/old-kernel.bin" $((off + 4))) || continue;
    off_struct=$(read_be32 "$AKHOME/old-kernel.bin" $((off + 8))) || continue;
    off_strings=$(read_be32 "$AKHOME/old-kernel.bin" $((off + 12))) || continue;
    version=$(read_be32 "$AKHOME/old-kernel.bin" $((off + 20))) || continue;
    [ "$total" -ge 40 ] || continue;
    [ "$off_struct" -lt "$total" ] || continue;
    [ "$off_strings" -lt "$total" ] || continue;
    case "$version" in 16|17) ;; *) continue;; esac;
    [ $((off + total)) -le "$old_size" ] || continue;
    best_off=$off;
    best_total=$total;
    # The appended DTB normally ends exactly at the kernel size.
    [ $((off + total)) -eq "$old_size" ] && break;
  done;

  [ "$best_off" ] || return 1;
  dd if="$AKHOME/old-kernel.bin" of="$AKHOME/stock.dtb" bs=1 skip="$best_off" count="$best_total" 2>/dev/null || return 1;
  return 0;
}

replace_kernel() {
  local source source_gz source_dtb source_name old_size ramdisk_size second_size dtb_size rec_size
  local new_size old_kernel_end new_kernel_end old_ramdisk_off new_ramdisk_off
  local old_second_off new_second_off old_dtb_off new_dtb_off
  local old_data_end new_data_end delta boot_size header_version header_size;

  old_size=$(read_u32 "$BOOTIMG" 8);
  ramdisk_size=$(read_u32 "$BOOTIMG" 16);
  second_size=$(read_u32 "$BOOTIMG" 24);
  BOOT_PAGE=$(read_u32 "$BOOTIMG" 36);
  header_version=$(read_u32 "$BOOTIMG" 40);
  header_size=$(read_u32 "$BOOTIMG" 1644);
  rec_size=$(read_u32 "$BOOTIMG" 1632);
  dtb_size=$(read_u32 "$BOOTIMG" 1648);
  case "$old_size:$ramdisk_size:$second_size:$BOOT_PAGE:$header_version:$header_size:$rec_size:$dtb_size" in
    *[!0-9:]*|:*|*::*) abort "Invalid Android boot header. Aborting...";;
  esac;
  [ "$BOOT_PAGE" -ge 512 ] || abort "Invalid boot page size. Aborting...";
  [ "$header_version" -le 2 ] || abort "Unsupported Android boot header version. Aborting...";
  [ "$header_size" -le "$BOOT_PAGE" ] || abort "Invalid boot header size. Aborting...";
  [ "$rec_size" -eq 0 ] || abort "Recovery DTBO boot layout is not supported by this installer. Aborting...";

  source_gz=;
  for source_name in Image.gz Image; do
    if [ -f "$AKHOME/$source_name" ]; then
      source_gz="$AKHOME/$source_name";
      break;
    fi;
  done;
  source_dtb=;
  for source_name in Image.gz-dtb Image-dtb zImage-dtb; do
    if [ -f "$AKHOME/$source_name" ]; then
      source_dtb="$AKHOME/$source_name";
      break;
    fi;
  done;
  [ "$source_gz" ] || [ "$source_dtb" ] || abort "No CPH2185/CPH2179 kernel image found. Aborting...";

  source=;
  if [ "$source_gz" = "$AKHOME/Image.gz" ] && extract_stock_dtb "$old_size" "$dtb_size"; then
    cat "$source_gz" "$AKHOME/stock.dtb" > "$AKHOME/kernel.new" || abort "Kernel/DTB merge failed. Aborting...";
    source="$AKHOME/kernel.new";
    ui_print "Keeping the stock appended DTB.";
  elif [ "$source_dtb" ]; then
    source="$source_dtb";
  else
    source="$source_gz";
  fi;
  [ "$source" ] || abort "No usable kernel image found. Aborting...";

  old_kernel_end=$((BOOT_PAGE + $(align_to_page "$old_size" "$BOOT_PAGE")));
  old_ramdisk_off=$old_kernel_end;
  old_second_off=$((old_ramdisk_off + $(align_to_page "$ramdisk_size" "$BOOT_PAGE")));
  old_dtb_off=$((old_second_off + $(align_to_page "$second_size" "$BOOT_PAGE")));
  old_data_end=$((old_dtb_off + $(align_to_page "$dtb_size" "$BOOT_PAGE")));
  boot_size=$(wc -c < "$BOOTIMG");
  [ "$old_data_end" -le "$boot_size" ] || abort "Stock boot image layout is invalid. Aborting...";

  new_size=$(wc -c < "$source");
  new_kernel_end=$((BOOT_PAGE + $(align_to_page "$new_size" "$BOOT_PAGE")));
  delta=$((new_kernel_end - old_kernel_end));
  new_ramdisk_off=$((old_ramdisk_off + delta));
  new_second_off=$((old_second_off + delta));
  new_dtb_off=$((old_dtb_off + delta));
  new_data_end=$((old_data_end + delta));
  [ "$new_data_end" -le "$boot_size" ] || abort "Boot partition has no room for the new kernel. Aborting...";

  ui_print "Stock kernel slot: $old_size bytes";
  ui_print "New kernel: $new_size bytes";
  if [ "$delta" -gt 0 ]; then
    ui_print "Expanding kernel area and moving ramdisk/DTB...";
  elif [ "$delta" -lt 0 ]; then
    ui_print "Shrinking kernel area and moving ramdisk/DTB...";
  fi;

  # Back up every component before moving anything. Rewriting them at the new
  # offsets also covers the shrink case and removes stale kernel bytes.
  copy_pages ramdisk.bin "$old_ramdisk_off" "$(align_to_page "$ramdisk_size" "$BOOT_PAGE")";
  copy_pages second.bin "$old_second_off" "$(align_to_page "$second_size" "$BOOT_PAGE")";
  copy_pages dtb.bin "$old_dtb_off" "$(align_to_page "$dtb_size" "$BOOT_PAGE")";

  cp -f "$BOOTIMG" "$AKHOME/boot-new.img" || abort "Boot image copy failed. Aborting...";
  zero_pages "$BOOT_PAGE" "$new_ramdisk_off";
  dd if="$source" of="$AKHOME/boot-new.img" bs=1 seek="$BOOT_PAGE" conv=notrunc 2>/dev/null || abort "Kernel write failed. Aborting...";
  write_pages ramdisk.bin "$new_ramdisk_off" "$(align_to_page "$ramdisk_size" "$BOOT_PAGE")";
  write_pages second.bin "$new_second_off" "$(align_to_page "$second_size" "$BOOT_PAGE")";
  write_pages dtb.bin "$new_dtb_off" "$(align_to_page "$dtb_size" "$BOOT_PAGE")";

  write_u32_le "$AKHOME/boot-new.img" 8 "$new_size" || abort "Boot header update failed. Aborting...";
}

dump_boot() {
  local magic;
  [ -e "$BLOCK" ] || abort "Invalid boot partition. Aborting...";
  dd if="$BLOCK" of="$BOOTIMG" bs=1048576 2>/dev/null || abort "Dumping boot image failed. Aborting...";
  magic=$(dd if="$BOOTIMG" bs=1 count=8 2>/dev/null);
  [ "$magic" = "ANDROID!" ] || abort "Stock boot image is not Android format. Aborting...";
}

write_boot() {
  replace_kernel;
  blockdev --setrw "$BLOCK" 2>/dev/null;
  dd if="$AKHOME/boot-new.img" of="$BLOCK" bs=1048576 2>/dev/null || abort "Flashing boot image failed. Aborting...";
}

file_size() {
  wc -c < "$1" 2>/dev/null | tr -d '[:space:]';
}

dump_boot;
write_boot;

# KernelSU fallback for 8 GiB zram when /data is already decrypted in recovery.
# The kernel also sets zram0 to 8 GiB and tries swapon itself; this script only
# runs the userspace path if the kernel did not manage to activate swap.
if [ -d /data/adb ] && [ -w /data/adb ]; then
  mkdir -p /data/adb/service.d 2>/dev/null;
  cat > /data/adb/service.d/azixur_zram.sh <<'AZIXUR_EOF'
#!/system/bin/sh
# azixur 8GB zram swap (fallback; the kernel does this at late_initcall)
(
  i=0
  while [ "$(getprop sys.boot_completed)" != "1" ] && [ $i -lt 180 ]; do
    sleep 2; i=$((i+1))
  done
  if [ "$(cat /sys/block/zram0/disksize 2>/dev/null)" = "8589934592" ] &&
     grep -q "/dev/block/zram0" /proc/swaps; then
    exit 0
  fi
  Z=/dev/block/zram0
  [ -e "$Z" ] || Z=/dev/zram0
  swapoff "$Z" 2>/dev/null
  echo 1 > /sys/block/zram0/reset 2>/dev/null
  echo lz4 > /sys/block/zram0/comp_algorithm 2>/dev/null
  echo 8589934592 > /sys/block/zram0/disksize 2>/dev/null
  mkswap "$Z" >/dev/null 2>&1
  swapon "$Z" 2>/dev/null
  echo 160 > /proc/sys/vm/swappiness
  echo 60 > /proc/sys/vm/direct_swappiness
  echo 8 > /sys/module/lowmemorykiller/parameters/almk_swap_ratio1 2>/dev/null
  echo 6 > /sys/module/lowmemorykiller/parameters/almk_totalram_ratio 2>/dev/null
) &
AZIXUR_EOF
  chmod 755 /data/adb/service.d/azixur_zram.sh 2>/dev/null;
  ui_print "Installed 8GB zram boot helper (KernelSU).";
fi;

ui_print "Falcon-AZ kernel installed.";
ui_print "Stock ramdisk and DTB were kept.";
