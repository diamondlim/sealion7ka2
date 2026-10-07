#!/bin/bash
# Kent's standing rule: after ANY modification on the KA2 box, the change must land on GitHub.
#
# The box has no push credentials, so this runs on the Hermes host: it pulls the changed files off the box and
# pushes them to diamondlim/sealion7ka2 @ longitudinal_off as one commit, via ka2_push_files.py (Git Data API -
# no clone; only the files that changed travel). The box's own updater hard-resets /data/openpilot to that
# branch, so an unpushed edit is work waiting to be erased - which is exactly how the lane-centre patch was
# lost overnight on 2026-09-27.
#
# Runs every 15 min from cron (no_agent): silent when the box is asleep or nothing changed; reports only when
# it pushes or fails. After changing anything on the box by hand, run this yourself.
set -uo pipefail

# Paths resolve under this host's Hermes home: the literals here used to be /config/.hermes (the
# container host), which made every run a silent no-op after the restore onto a plain Linux host.
HERMES=${HERMES_HOME:-$HOME/.hermes}
KM=$HERMES/keys/ka2_ed25519
REPO=diamondlim/sealion7ka2
BRANCH=longitudinal_off
STATE=$HERMES/repos/ka2_sync_state              # last-synced copy of each file, so "nothing changed" is cheap
TMP=$HERMES/cache/scratch/ka2_sync              # where the box's current versions land
LOG=$HERMES/logs/ka2_sync.log
BOX_TREE=/data/openpilot
mkdir -p "$STATE" "$TMP" "$(dirname "$LOG")"
log() { printf '%s %s\n' "$(date -Is)" "$*" >>"$LOG"; }

# --- 1. is the box up? (short timeouts: the car being off is the normal case, not an error) -----------
# The tailnet address is only reachable through the userspace client's SOCKS proxy when one is actually
# listening. This host's tailscaled runs in kernel mode, so a hardcoded ProxyCommand makes every probe fail
# and the box read as asleep while it is answering on the tailnet. Probe the port; add the option only if
# something answers there.
SSH_O=(-i "$KM" -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=10 -o LogLevel=ERROR)
if timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/11055' 2>/dev/null; then
  SSH_O+=(-o 'ProxyCommand=nc -X 5 -w 10 -x 127.0.0.1:11055 %h %p')
fi
ssh_try() { # $1 host  $2 command
  timeout 30 ssh "${SSH_O[@]}" kommu@"$1" "$2" 2>/dev/null
}
BOX=""
for cand in 10.0.1.231 10.0.3.95 100.121.248.8; do   # home LAN, hostel LAN, then the tailnet
  [ "$(ssh_try "$cand" 'echo OK')" = "OK" ] && { BOX=$cand; break; }
done
if [ -z "$BOX" ]; then log "box unreachable (car off) - nothing to do"; exit 0; fi

# --- 2. what to look at ------------------------------------------------------------------------------
# The mirror list comes first, because the tree scan below has to know which repo paths it already owns.
# An in-tree copy of a mirror's repo path (the fork carries a stale ka2_hermes/) otherwise reads as a
# second source of the same file, with different bytes from the live one: the two then take turns
# overwriting the state file, so every run saw a "change" and pushed the same path again - every 15 min.
MIRRORS=(
  "/data/hermes/ka2_vision_acc.py:ka2_hermes/ka2_vision_acc.py"
  "/data/hermes/bt_settings_service.py:ka2_hermes/bt_settings_service.py"
  "/usr/kommu/hermes_ka2_patch.sh:ka2_hermes/hermes_ka2_patch.sh"
  "/data/hermes/patches/patch_decouple.py:ka2_hermes/patches/patch_decouple.py"
  "/data/hermes/patches/patch_bias.py:ka2_hermes/patches/patch_bias.py"
  "/data/hermes/ka2_xtra_inject.py:ka2_hermes/ka2_xtra_inject.py"
  "/data/hermes/ka2_xtra_refresh.sh:ka2_hermes/ka2_xtra_refresh.sh"
  "/data/hermes/ka2_gnss_at_pub.py:ka2_hermes/ka2_gnss_at_pub.py"
  "/data/hermes/ka2_gnss_pub.py:ka2_hermes/ka2_gnss_pub.py"
  "/data/hermes/log_summary.py:ka2_hermes/log_summary.py"
  "/data/hermes/ka2_nas_push.py:ka2_hermes/ka2_nas_push.py"
  "/data/hermes/systemd/ka2-nas-push.service:ka2_hermes/systemd/ka2-nas-push.service"
  "/data/hermes/systemd/ka2-nas-push.timer:ka2_hermes/systemd/ka2-nas-push.timer"
  "/data/hermes/ka2_gnss_engine_up.sh:ka2_hermes/ka2_gnss_engine_up.sh"
  "/data/hermes/ka2_gnss_engine_install.sh:ka2_hermes/ka2_gnss_engine_install.sh"
  "/data/hermes/systemd/ka2-gnss-engine.service:ka2_hermes/systemd/ka2-gnss-engine.service"
  "/data/hermes/systemd/ka2-gnss-engine.timer:ka2_hermes/systemd/ka2-gnss-engine.timer"
  "/data/hermes/ka2_press_ab.py:ka2_hermes/ka2_press_ab.py"
  "/data/hermes/ka2_restore_vis_rows.py:ka2_hermes/ka2_restore_vis_rows.py"
  "/data/hermes/ka2_sng_port.py:ka2_hermes/ka2_sng_port.py"
  "/data/hermes/ka2_restore_vis_all.sh:ka2_hermes/ka2_restore_vis_all.sh"
  # the tool every press path funnels through (the app via bt_settings_service, the A/B harness, vision-acc);
  # missing from this list it was only ever mirrored from the fork's stale in-tree copy, so the repo showed
  # the pre-gate tool while the box ran the gated one.
  "/data/hermes/ka2_acc_press.py:ka2_hermes/ka2_acc_press.py"
  "/data/hermes/ka2_acc_watch_press.py:ka2_hermes/ka2_acc_watch_press.py"
  # the live tuning: which knobs are on (VIS_TURN_ACC_ENABLED) and at what values. Without this the repo
  # shows the code that reads the tuning but not the tuning itself, so "is auto-slow on?" is unanswerable
  # from GitHub.
  "/data/hermes/tuning.json:ka2_hermes/tuning.json"
  # the sync mechanism itself, kept on the box as a record: without it the repo holds every mirrored file
  # but not the rule that decides which files those are.
  "/data/hermes/ka2_sync_to_github.sh:ka2_hermes/ka2_sync_to_github.sh"
)

# 2a. whatever git sees as modified/untracked inside the openpilot tree (junk and binaries filtered out),
# minus every path the mirror list above already owns. The live copy of a mirrored file is the one under
# /data/hermes; the copy that happens to be sitting in the tree is stale by construction, so it must never
# be treated as its own source of change.
MIRROR_PATHS=()
for m in "${MIRRORS[@]}"; do MIRROR_PATHS+=("${m#*:}"); done
mapfile -t TREE_PATHS < <(ssh_try "$BOX" "cd $BOX_TREE && git status --porcelain" \
  | awk '{ $1=""; sub(/^ /,""); print }' \
  | grep -vE '(__pycache__|\.pyc$|\.pyo$|\.log$|\.zst$|\.mp4$|\.db$|\.so$)' | grep -v '^$' || true)
TREE_KEEP=()
for p in "${TREE_PATHS[@]:-}"; do
  [ -n "$p" ] || continue
  owned=0
  for mp in "${MIRROR_PATHS[@]}"; do [ "$p" = "$mp" ] && { owned=1; break; }; done
  if [ "$owned" -eq 1 ]; then log "tree path $p is owned by the mirror list - using that copy instead"; continue; fi
  TREE_KEEP+=("$p")
done
TREE_PATHS=("${TREE_KEEP[@]:-}")

rm -f "$TMP/paths"
changed=()
add_if_new() { # $1 local file, $2 path in the repo
  [ -s "$1" ] || return 0
  key=$(printf '%s' "$2" | tr '/' '_')
  for seen in "${changed[@]:-}"; do [ "$seen" = "$2" ] && return 0; done   # one entry per repo path
  if [ -f "$STATE/$key" ] && cmp -s "$1" "$STATE/$key"; then return 0; fi   # unchanged since the last sync
  cp -f "$1" "$STATE/$key"
  changed+=("$2")
  printf '%s\n' "$2" >>"$TMP/paths"
}
for p in "${TREE_PATHS[@]:-}"; do
  [ -n "$p" ] || continue
  key=$(printf '%s' "$p" | tr '/' '_')
  if ssh_try "$BOX" "cat '$BOX_TREE/$p'" >"$TMP/$key" 2>/dev/null; then add_if_new "$TMP/$key" "$p"; fi
done
for m in "${MIRRORS[@]}"; do
  src="${m%%:*}"; dst="${m#*:}"; key=$(printf '%s' "$dst" | tr '/' '_')
  if ssh_try "$BOX" "cat '$src'" >"$TMP/$key" 2>/dev/null; then add_if_new "$TMP/$key" "$dst"; fi
done

# --- 3. repo hygiene: the deploy timer was once committed as a stray error message, not a unit file ----
if ! ssh_try "$BOX" "cat '$BOX_TREE/ka2_hermes/deploy/hermes-ka2-patch.timer'" 2>/dev/null | grep -q '^\[Unit\]'; then
  {
    echo '[Unit]'
    echo 'Description=Hermes KA2 patch (re-apply the box tree edits after an updater reset)'
    echo
    echo '[Timer]'
    echo 'OnBootSec=90'
    echo 'OnUnitActiveSec=10min'
    echo 'Unit=hermes-ka2-patch.service'
    echo
    echo '[Install]'
    echo 'WantedBy=timers.target'
  } >"$TMP/ka2_hermes_deploy_hermes-ka2-patch.timer"
  add_if_new "$TMP/ka2_hermes_deploy_hermes-ka2-patch.timer" "ka2_hermes/deploy/hermes-ka2-patch.timer"
fi

if [ "${#changed[@]}" -eq 0 ]; then log "box up, nothing changed since last sync"; exit 0; fi

# --- 4. one commit for all of it ---------------------------------------------------------------------
manifest="$TMP/manifest.json"
python3 - "$manifest" "$TMP/paths" "$TMP" <<'PY'
import json, sys
manifest, paths, tmp = sys.argv[1], sys.argv[2], sys.argv[3]
out = []
for p in dict.fromkeys(open(paths).read().split()):
    out.append({"path": p, "file": tmp + "/" + p.replace("/", "_")})
json.dump(out, open(manifest, "w"), indent=1)
PY

GITHUB_TOKEN=$(gh auth token 2>/dev/null); export GITHUB_TOKEN
if [ -z "${GITHUB_TOKEN:-}" ]; then log "no gh token - cannot push"; echo "KA2 sync: changes found but no GitHub token available."; exit 1; fi
files="${changed[*]}"
if ! out=$(timeout 240 python3 "$HERMES/scripts/ka2_push_files.py" --repo "$REPO" --branch "$BRANCH" \
             --message "box sync: $files" "$manifest" 2>&1); then
  log "PUSH FAILED: $out"; echo "KA2 sync FAILED: $out"; exit 1
fi
sha="${out%% *}"

# --- 5. verify from GitHub itself, never from anything local -----------------------------------------
hits=$(timeout 40 curl -sS "https://raw.githubusercontent.com/$REPO/$BRANCH/selfdrive/controls/controlsd.py" 2>/dev/null \
       | grep -c 'LANE_CORRECTION_DECOUPLE' || true)
log "pushed $sha: $files ; github controlsd decouple hits: ${hits:-0}"
echo "KA2 box changes pushed to GitHub — $REPO@$BRANCH $sha"
echo "files: $files"
if printf '%s' "$files" | grep -q 'controlsd.py' && [ "${hits:-0}" -eq 0 ]; then
  echo "WARNING: pushed, but the GitHub copy of controlsd.py shows no decouple flag — check the box's file."
else
  echo "verified by reading the file back from raw.githubusercontent.com."
fi
