#!/bin/bash
# Reload controlsd (a full stack restart - the manager does not respawn a process that exited cleanly, and
# controlsd is not flagged restart_if_crash) once the car is parked, so the lane-centre bias patch goes live
# without interrupting assist mid-drive. Started with nohup, so it survives the ssh session that launched it.
LOG=/data/hermes/reload_bias.log
exec >>"$LOG" 2>&1
echo "$(date -Is) waiting for the car to park (IsOnroad=0 twice, 10 s apart) before reloading controlsd"
zero=0
for _ in $(seq 1 960); do            # 960 * 15 s = 4 h
  if [ "$(cat /data/params/d/IsOnroad 2>/dev/null)" = "0" ]; then
    zero=$((zero + 1))
    if [ "$zero" -ge 2 ]; then
      echo "$(date -Is) parked - restarting the stack"
      systemctl restart kommu.service
      sleep 30
      echo "$(date -Is) stack restarted; controlsd process:"
      for p in $(ls /proc | grep -E '^[0-9]+$'); do
        a=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)
        case "$a" in
          *controlsd*) echo "  pid $p started $(ps -o lstart= -p "$p") :: ${a:0:90}" ;;
        esac
      done
      echo "$(date -Is) lane tuning as the new controlsd logged it:"
      grep -ho 'lane tuning now: {[^}]*}' $(ls -t /data/log/swaglog* 2>/dev/null | head -3) 2>/dev/null | tail -2
      echo "$(date -Is) done"
      exit 0
    fi
  else
    zero=0
  fi
  sleep 15
done
echo "$(date -Is) gave up waiting for the car to park; controlsd still runs the pre-bias code"
