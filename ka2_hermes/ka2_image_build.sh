#!/usr/bin/env bash
# Builds a restorable image of THIS box, locally, so the slow link is never in the path.
#
# Includes only what is needed to rebuild the box:
#   - the partition table and the small partitions (uboot, misc, boot, recovery, backup, cache, persist)
#   - the root filesystem as files (the overlay's merged view of the running system)
#   - /data as files, minus the openpilot tree (it lives on GitHub) and the SD card mount
# Deliberately excluded:
#   - /dev/mmcblk1 entirely (the SD card, 12 GB of route logs) - Kent's call, 27 Sep
#   - the inactive rootfs_b slot (p10): a restore flashes Kommu's image first, then lays this over it
#
# Why the root is captured as files rather than with e2image: e2image's sparse output silently loses its
# holes when it is piped (6 MB came out instead of 4 GB), and a 4.3 GB sparse file will not fit on /data
# next to its own compressed copy. The bootloader, kernel and partition table come from the partitions
# above, so the root does not need to be a byte-image.
#
# Runs detached on the box (nohup/setsid), so a dropped ssh cannot kill it mid-write.
set -uo pipefail

STAMP=$(date +%Y%m%d-%H%M%S)
OUT=/data/ka2-image-$STAMP

echo "=== ka2 image build $STAMP"
echo "onroad=$(cat /data/params/d/IsOnroad 2>/dev/null)  load=$(cut -d' ' -f1-3 /proc/loadavg)  free_before=$(df -h /data | awk 'NR==2{print $4}')"
if [ "$(cat /data/params/d/IsOnroad 2>/dev/null)" != "0" ]; then
  echo "ABORT: onroad reads $(cat /data/params/d/IsOnroad 2>/dev/null) - not imaging while the car is live"
  exit 4
fi
mkdir -p "$OUT" || { echo "ABORT: cannot create $OUT"; exit 5; }

echo "--- partition table and manifest"
sudo sfdisk --dump /dev/mmcblk0 > "$OUT/partition-table.txt" 2>/dev/null
{
  echo "built: $STAMP"; echo "kernel: $(uname -a)"; echo "cmdline: $(cat /proc/cmdline)"; echo
  echo "--- block devices"; lsblk -a -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT 2>/dev/null || cat /proc/partitions
  echo "--- mounts"; df -h
  echo "--- openpilot state (the tree itself is on GitHub, so it is not inside this image)"
  git -C /data/openpilot remote -v 2>/dev/null
  echo "HEAD: $(git -C /data/openpilot rev-parse HEAD 2>/dev/null) on $(git -C /data/openpilot rev-parse --abbrev-ref HEAD 2>/dev/null)"
  echo "branch param: $(cat /data/params/d/UpdaterTargetBranch 2>/dev/null)"
} > "$OUT/manifest.txt" 2>&1

echo "--- small partitions 1-9"
for p in 1 2 3 4 5 6 7 8 9; do
  sz=$(sudo blockdev --getsize64 /dev/mmcblk0p$p 2>/dev/null || echo 0)
  echo "    p$p ($((sz/1048576)) MB)"
  sudo dd if=/dev/mmcblk0p$p bs=4M status=none | zstd -q -3 -T4 -f -o "$OUT/part$p.img.zst" -
done

echo "--- the root filesystem as files (the other mounts are skipped)"
sudo tar -c --one-file-system -C / \
     --exclude=./proc --exclude=./sys --exclude=./dev --exclude=./run --exclude=./tmp \
     --exclude=./root-ro --exclude=./userdata --exclude=./data --exclude=./boot \
     --exclude=./persist --exclude=./cache --exclude=./media --exclude=./tmprw . 2>/dev/null \
  | zstd -q -3 -T4 -f -o "$OUT/root-files.tar.zst" -
echo "    root files: $(du -h "$OUT/root-files.tar.zst" 2>/dev/null | cut -f1)"

echo "--- /data as files (openpilot tree and the SD mount excluded)"
sudo tar -c -C /data --one-file-system \
     --exclude=./media --exclude=./safe_staging --exclude=./openpilot \
     --exclude='./ka2-image-*' . 2>/dev/null \
  | zstd -q -3 -T4 -f -o "$OUT/data-files.tar.zst" -
echo "    data files: $(du -h "$OUT/data-files.tar.zst" 2>/dev/null | cut -f1)"

cat > "$OUT/RESTORE.md" <<'MD'
# Restoring this box

What each file is:

- `partition-table.txt`  - the GPT layout of /dev/mmcblk0 (sfdisk format). Restore this first on a blank card.
- `part1..part9.img.zst` - the small partitions, raw: uboot, misc, boot, recovery, backup, cache, persist.
- `root-files.tar.zst`   - the root filesystem as files (the running system's merged overlay view).
- `data-files.tar.zst`   - /data as files. Not included: the openpilot tree (see below) and the SD card.
- `manifest.txt`         - kernel, layout, mounts and openpilot state at build time.
- `SHA256SUMS`           - checksums. Verify before restoring anything.

Suggested order:

1. Blank card, then the layout:  sudo sfdisk /dev/mmcblk0 < partition-table.txt
2. The small partitions:  for p in 1 2 3 4 5 6 7 8 9; do
       zstd -dc part$p.img.zst | sudo dd of=/dev/mmcblk0p$p bs=4M status=progress
   done
3. The root, over a freshly flashed Kommu root partition:
       zstd -dc root-files.tar.zst | sudo tar -x -C / --numeric-owner --xattrs
4. /data:  zstd -dc data-files.tar.zst | sudo tar -x -C /data --numeric-owner --xattrs
5. The openpilot tree comes from GitHub, not from here:
       git clone -b longitudinal_off https://github.com/diamondlim/sealion7ka2 /data/openpilot
   Then the branch the updater tracks:  echo longitudinal_off > /data/params/d/UpdaterTargetBranch
6. Kommu's boot patch script (/usr/kommu/hermes_ka2_patch.sh) and the systemd units
   (ka2-vision-acc, ka2bt, hermes-ka2-patch) are in the private repo diamondlim/hermes-backup.
7. Check:  systemctl status ka2-vision-acc ka2bt   - and the app rows should reappear.

Notes:

- The inactive rootfs_b slot (p10) is NOT here. Flash Kommu's own image first, then lay this over it.
- Wi-Fi credentials and SSH keys live under /data/params and ARE in data-files.tar.zst, so this image
  contains secrets: keep it somewhere you would keep the box itself.
- The SD card (/dev/mmcblk1, route logs) is deliberately excluded, as is the openpilot tree (GitHub).
MD

echo "--- checksums"
( cd "$OUT" && sudo sha256sum ./* > SHA256SUMS 2>/dev/null )
sudo chown -R kommu:kommu "$OUT" 2>/dev/null
echo "image dir: $OUT"
du -sh "$OUT" 2>/dev/null
du -h "$OUT"/* 2>/dev/null
echo "free_after=$(df -h /data | awk 'NR==2{print $4}')"
echo "BUILD_DONE"
