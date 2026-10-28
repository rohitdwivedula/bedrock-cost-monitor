"""Incrementally pull Bedrock invocation logs from all four buckets into SQLite.

Uses `aws s3 sync` (parallel) into a local mirror, then parses only new files.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python3 -I drops this
import json, gzip, sqlite3, subprocess, sys, os, glob, pathlib

from dashconfig import LOG_BUCKETS as BUCKETS, ACCOUNT_ID
DB, MIRROR = "spend.db", "mirror"

def principal(arn):
    """Attributable identity from a caller ARN.

    The last segment is wrong for assumed roles: in
    arn:aws:sts::acct:assumed-role/ROLE/SESSION it is the session name, so one
    role fans out into a row per session. Use the role instead.
    """
    if not arn:
        return ""
    parts = arn.split("/")
    if ":assumed-role/" in arn and len(parts) >= 2:
        return parts[1]
    return parts[-1] or arn

def db():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS calls(
      request_id TEXT PRIMARY KEY, ts TEXT, day TEXT, user TEXT, model_id TEXT,
      region TEXT, operation TEXT,
      in_tok INT, out_tok INT, cache_read INT, cache_write INT);
    CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY);
    CREATE INDEX IF NOT EXISTS i_day ON calls(day);
    CREATE INDEX IF NOT EXISTS i_user ON calls(user);
    """)
    return c

# Bedrock writes one object per call, so these buckets reach millions of keys.
# `aws s3 sync` never re-downloads what we already have, but it must still LIST
# the whole bucket to find out, and LIST is billed per 1000 keys. At ~13k
# objects/day a full sync every 5 minutes costs cents today and hundreds of
# dollars a month within the year.
#
# So the window is derived from what we already have rather than being a fixed
# constant:
#   - database empty  -> cold start, go back COLD_START_DAYS
#   - database populated -> from the newest record we hold, up to today
# A gap of any length therefore heals itself on the next run, and the steady
# state is a single day prefix. Set DASH_LOOKBACK_DAYS to force a wider sweep.
COLD_START_DAYS = int(os.environ.get("DASH_COLD_START_DAYS", "90"))
OVERLAP_DAYS    = 1          # objects can be delivered after their date rolls over

def prefixes(con=None):
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date()
    forced = os.environ.get("DASH_LOOKBACK_DAYS")
    if forced:
        start = today - dt.timedelta(days=int(forced))
    else:
        newest = None
        if con is not None:
            row = con.execute("SELECT MAX(ts) FROM calls").fetchone()
            newest = row[0] if row else None
        if newest:
            start = dt.date.fromisoformat(newest[:10]) - dt.timedelta(days=OVERLAP_DAYS)
        else:
            start = today - dt.timedelta(days=COLD_START_DAYS)
    span = (today - start).days
    return [(start + dt.timedelta(days=i)).strftime("%Y/%m/%d") for i in range(span + 1)]

# Beyond this many days, one full-bucket sync beats N per-day syncs: each
# per-day sync is a separate CLI process (~0.5s of startup), so 91 prefixes
# across 4 buckets is 364 invocations and several minutes, while a single
# sync pays one enumeration. Narrow windows stay prefix-scoped because
# enumeration is the thing that costs money as the bucket grows.
FULL_SYNC_THRESHOLD_DAYS = 7

def sync(con=None):
    days = prefixes(con)
    if len(days) > FULL_SYNC_THRESHOLD_DAYS:
        print(f"wide window ({len(days)} days: {days[0]} .. {days[-1]}); "
              f"doing one full sync per bucket", file=sys.stderr)
        for b in BUCKETS:
            src  = f"s3://{b}/AWSLogs/{ACCOUNT_ID}/BedrockModelInvocationLogs/"
            dest = os.path.join(MIRROR, b, f"AWSLogs/{ACCOUNT_ID}/BedrockModelInvocationLogs")
            pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["aws","s3","sync",src,dest,
                                "--exclude","*","--include","*.gz",
                                "--exclude","*/data/*","--only-show-errors"],
                               capture_output=True, text=True)
            if r.returncode: print(f"  sync {b}: {r.stderr[:160]}", file=sys.stderr)
        return
    print(f"syncing {len(days)} day prefix(es): {days[0]}"
          + (f" .. {days[-1]}" if len(days) > 1 else ""), file=sys.stderr)
    for b in BUCKETS:
        for day in days:
            # AWSLogs/<account>/BedrockModelInvocationLogs/<region>/<Y/M/D>/...
            src = f"s3://{b}/AWSLogs/{ACCOUNT_ID}/BedrockModelInvocationLogs/"
            dest = os.path.join(MIRROR, b, f"AWSLogs/{ACCOUNT_ID}/BedrockModelInvocationLogs")
            pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["aws","s3","sync",src,dest,
                                "--exclude","*","--include",f"*/{day}/*.gz",
                                "--exclude","*/data/*","--only-show-errors"],
                               capture_output=True, text=True)
            if r.returncode: print(f"  sync {b} {day}: {r.stderr[:160]}", file=sys.stderr)

def main():
    c = db(); cur = c.cursor()
    sync(c)
    seen = {r[0] for r in cur.execute("SELECT key FROM seen")}
    files = [f for f in glob.glob(f"{MIRROR}/**/*.gz", recursive=True) if "/data/" not in f]
    todo = [f for f in files if f not in seen]
    print(f"{len(files)} mirrored log files, {len(todo)} new", file=sys.stderr)
    added = 0
    for f in todo:
        try: raw = gzip.open(f,"rt").read()
        except Exception as e: print("  skip",f,e, file=sys.stderr); continue
        for line in raw.splitlines():
            line = line.strip()
            if not line: continue
            try: d = json.loads(line)
            except Exception: continue
            for r in (d if isinstance(d,list) else [d]):
                rid = r.get("requestId")
                if not rid: continue
                arn = (r.get("identity") or {}).get("arn","")
                inp = r.get("input") or {}; out = r.get("output") or {}
                ts = r.get("timestamp","")
                # Bedrock emits TWO records per streaming call sharing one
                # requestId: one with zeroed counts and one with the real
                # ones, in separate S3 objects. Take the max of each field so
                # whichever arrives first, and whichever run it arrives in,
                # the token-bearing values win. INSERT OR IGNORE here silently
                # dropped ~50% of all tokens.
                cur.execute("""
                    INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(request_id) DO UPDATE SET
                      ts         = min(calls.ts, excluded.ts),
                      day        = substr(min(calls.ts, excluded.ts),1,10),
                      in_tok     = max(calls.in_tok,      excluded.in_tok),
                      out_tok    = max(calls.out_tok,     excluded.out_tok),
                      cache_read = max(calls.cache_read,  excluded.cache_read),
                      cache_write= max(calls.cache_write, excluded.cache_write),
                      model_id   = CASE WHEN excluded.in_tok + excluded.out_tok
                                           + excluded.cache_read + excluded.cache_write > 0
                                        THEN excluded.model_id ELSE calls.model_id END,
                      region     = CASE WHEN excluded.region != '' THEN excluded.region
                                        ELSE calls.region END""",(
                    rid, ts, ts[:10], principal(arn),
                    str(r.get("modelId","")).split("/")[-1],
                    r.get("region",""), r.get("operation",""),
                    inp.get("inputTokenCount",0) or 0, out.get("outputTokenCount",0) or 0,
                    inp.get("cacheReadInputTokenCount",0) or 0,
                    inp.get("cacheWriteInputTokenCount",0) or 0))
                added += 1
        cur.execute("INSERT OR IGNORE INTO seen VALUES(?)", (f,))
    c.commit()
    n = cur.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    print(f"applied {added} records; {n} distinct requests in db", file=sys.stderr)

if __name__=="__main__": main()
