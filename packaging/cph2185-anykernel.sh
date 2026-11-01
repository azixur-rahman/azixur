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

replace_kernel() {
  local source old_size ramdisk_size second_size dtb_size rec_size
  local new_size old_kernel_end new_kernel_end old_ramdisk_off new_ramdisk_off
  local old_second_off new_second_off old_rec_off new_rec_off old_dtb_off new_dtb_off
  local old_data_end new_data_end delta boot_size header_version header_size;

  source=;
  for source_name in Image.gz-dtb Image-dtb Image.gz Image zImage-dtb zImage; do
    if [ -f "$AKHOME/$source_name" ]; then
      source="$AKHOME/$source_name";
      break;
    fi;
  done;
  [ "$source" ] || abort "No CPH2185 kernel image found. Aborting...";

  old_size=$(read_u32 "$BOOTIMG" 8);
  ramdisk_size=$(read_u32 "$BOOTIMG" 16);
  second_size=$(read_u32 "$BOOTIMG" 24);
  BOOT_PAGE=$(read_u32 "$BOOTIMG" 36);
  header_version=$(read_u32 "$BOOTIMG" 40);
  header_size=$(read_u32 "$BOOTIMG" 1644);
  rec_size=$(read_u32 "$BOOTIMG" 1632);
  dtb_size=$(read_u32 "$BOOTIMG" 1648);
  new_size=$(wc -c < "$source");
  case "$old_size:$ramdisk_size:$second_size:$BOOT_PAGE:$header_version:$header_size:$rec_size:$dtb_size:$new_size" in
    *[!0-9:]*|:*|*::*) abort "Invalid Android boot header. Aborting...";;
  esac;
  [ "$BOOT_PAGE" -ge 512 ] || abort "Invalid boot page size. Aborting...";
  [ "$header_version" -le 2 ] || abort "Unsupported Android boot header version. Aborting...";
  [ "$header_size" -le "$BOOT_PAGE" ] || abort "Invalid boot header size. Aborting...";
  [ "$rec_size" -eq 0 ] || abort "Recovery DTBO boot layout is not supported by this installer. Aborting...";

  old_kernel_end=$((BOOT_PAGE + $(align_to_page "$old_size" "$BOOT_PAGE")));
  old_ramdisk_off=$old_kernel_end;
  old_second_off=$((old_ramdisk_off + $(align_to_page "$ramdisk_size" "$BOOT_PAGE")));
  old_dtb_off=$((old_second_off + $(align_to_page "$second_size" "$BOOT_PAGE")));
  old_data_end=$((old_dtb_off + $(align_to_page "$dtb_size" "$BOOT_PAGE")));
  boot_size=$(wc -c < "$BOOTIMG");
  [ "$old_data_end" -le "$boot_size" ] || abort "Stock boot image layout is invalid. Aborting...";

  new_kernel_end=$((BOOT_PAGE + $(align_to_page "$new_size" "$BOOT_PAGE")));
  delta=0;
  if [ "$new_size" -gt "$old_size" ]; then
    delta=$((new_kernel_end - old_kernel_end));
  fi;
  new_ramdisk_off=$((old_ramdisk_off + delta));
  new_second_off=$((old_second_off + delta));
  new_rec_off=0;
  new_dtb_off=$((old_dtb_off + delta));
  new_data_end=$((old_data_end + delta));
  [ "$new_data_end" -le "$boot_size" ] || abort "Boot partition has no room for the new kernel. Aborting...";

  ui_print "Stock kernel slot: $old_size bytes";
  ui_print "New kernel: $new_size bytes";
  if [ "$delta" -gt 0 ]; then
    ui_print "Expanding kernel area and moving ramdisk/DTB...";
    copy_pages ramdisk.bin "$old_ramdisk_off" "$(align_to_page "$ramdisk_size" "$BOOT_PAGE")";
    copy_pages second.bin "$old_second_off" "$(align_to_page "$second_size" "$BOOT_PAGE")";
    copy_pages dtb.bin "$old_dtb_off" "$(align_to_page "$dtb_size" "$BOOT_PAGE")";
  fi;

  cp -f "$BOOTIMG" "$AKHOME/boot-new.img" || abort "Boot image copy failed. Aborting...";
  zero_pages "$BOOT_PAGE" "$new_ramdisk_off";
  dd if="$source" of="$AKHOME/boot-new.img" bs=1 seek="$BOOT_PAGE" conv=notrunc 2>/dev/null || abort "Kernel write failed. Aborting...";
  if [ "$delta" -gt 0 ]; then
    write_pages ramdisk.bin "$new_ramdisk_off" "$(align_to_page "$ramdisk_size" "$BOOT_PAGE")";
    write_pages second.bin "$new_second_off" "$(align_to_page "$second_size" "$BOOT_PAGE")";
    write_pages dtb.bin "$new_dtb_off" "$(align_to_page "$dtb_size" "$BOOT_PAGE")";
  fi;
  if [ "$new_size" -gt "$old_size" ]; then
    write_u32_le "$AKHOME/boot-new.img" 8 "$new_size" || abort "Boot header update failed. Aborting...";
  fi;
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
ui_print "Falcon-AZ kernel installed.";
ui_print "Stock ramdisk and DTB were kept.";
