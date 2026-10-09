#!/usr/bin/env bash
# MVSEP quota drain watcher v5 (issue #247 scaffolding).
# Gate on /api/app/separation_history (zero quota cost, paginated).
# FAIL-SAFE: any API error / unexpected shape / unreachable -> return to
# probe-by-attempt (one real separation try), never "0 slots -> stall".
set -u
cd /home/mhuang/Development/sow_let_ai_design_lrc_automation
SLOTS_FREE=${SLOTS_FREE:-4}
PY=lab/poc-scripts/.venv/bin/python
RUN="$PY lab/poc-scripts/run_stem_cache.py"
echo "quota-drain watcher v5 started $(date -u +%FT%TZ) (gate: >=$SLOTS_FREE slots, fail-safe probe)"

count_free() {
  $PY - <<'PYEOF'
import json, os, urllib.request, datetime, sys
tok=os.environ['MVSEP_API_KEY']
now=datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
ts=lambda s: datetime.datetime.strptime(s,'%Y-%m-%d %H:%M:%S')
n=0; start=1
try:
    while True:                      # paginate so the 24h count can't truncate
        u=(f"https://mvsep.com/api/app/separation_history"
           f"?api_token={tok}&start={start}&limit=100")
        body=json.load(urllib.request.urlopen(u, timeout=30))
        if not isinstance(body, dict) or not isinstance(body.get('data'), list):
            print(-1); sys.exit(0)   # unexpected shape -> unknown
        page=body['data']
        if not page: break
        n+=sum(1 for e in page
               if isinstance(e, dict) and e.get('created_at')
               and (now-ts(e['created_at'])).total_seconds()<86400)
        if len(page)<100: break
        start+=100
        if start>2000: break
except Exception:
    print(-1); sys.exit(0)           # any error -> unknown, not 0
print(max(0,50-n))
PYEOF
}

next_failed() {
  $PY -c "
import json
m=json.load(open('lab/poc-scripts/eval/lrc_truth/stem_cache/manifest.json'))['songs']
f=[k for k,v in m.items() if v['status']=='failed']
print(f[0] if f else '')
"
}

while true; do
  if [ -z "$(next_failed)" ]; then echo "watcher: cache complete"; break; fi
  FREE=$(count_free)
  if [ "$FREE" -lt 0 ]; then
    # fail-safe: fall back to probing with one real attempt
    PROBE=$(next_failed)
    echo "watcher: history API unknown - probing $PROBE at $(date -u +%FT%TZ)"
    if $RUN --song-id "$PROBE" >/dev/null 2>&1; then
      echo "watcher: probe succeeded - draining review queue"
      timeout 5400 $RUN --set review_queue >/dev/null 2>&1
      echo "watcher: drain pass finished $(date -u +%FT%TZ)"
    else
      echo "watcher: probe failed (quota exhausted or error)"
    fi
    sleep 900; continue
  fi
  echo "watcher: free slots=$FREE at $(date -u +%FT%TZ)"
  if [ "$FREE" -ge "$SLOTS_FREE" ]; then
    echo "watcher: $FREE slots free - draining review queue"
    timeout 7200 $RUN --set review_queue >/dev/null 2>&1
    echo "watcher: drain pass finished $(date -u +%FT%TZ)"
  fi
  sleep 900
done
