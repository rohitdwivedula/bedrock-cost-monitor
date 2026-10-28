#!/usr/bin/env python3
"""Dashboard origin server.

Endpoints:
  GET  /api/data?from=&to=&grain=            aggregated rows for one window
  POST /api/refresh                           rebuild from S3, throttled
  everything else                             static files from this directory

Aggregation happens here, in SQL, at the interval the client asked for. The
browser only draws. Everything here is UTC: bounds in, UTC bucket keys out. The
client does all local-calendar bucketing, because the browser knows its own
timezone for any instant and a single numeric offset does not survive a
daylight-saving transition.
"""
import http.server, socketserver, subprocess, sqlite3, json, os, re, threading, time, sys
import datetime as dt
from urllib.parse import urlparse, parse_qs

PORT     = int(os.environ.get("DASH_PORT", "8080"))
HERE     = os.path.dirname(os.path.abspath(__file__))
DB       = os.path.join(HERE, "spend.db")
RATES    = os.path.join(HERE, "rates.json")
MIN_GAP  = 60       # seconds between accepted refreshes
MAXBUCKS = 400      # coarsen rather than return an undrawable number of points

FMT = {"minute": "%Y-%m-%d %H:%M", "hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d"}
COARSER = {"minute": "hour", "hour": "day", "day": "day"}

_lock = threading.Lock()
_last_refresh = 0.0

def rates():
    try:
        with open(RATES) as f: return json.load(f)
    except Exception: return {}

def default_interval(hours):
    """Granularity that suits the window: minutes only for short ranges."""
    if hours is None: return "hour"          # nothing to go on; hour is the safe middle
    if hours > 24 * 45: return "day"
    if hours > 6:  return "hour"
    return "minute"

_UTC = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$')
GRAINS = {"minute": "%Y-%m-%dT%H:%M", "hour": "%Y-%m-%dT%H:00"}
MAXROWS = 20000          # safety valve, not calendar logic

def query(frm, to, grain):
    """(rows, grain_used, span, unpriced, list_priced).

    Everything here is UTC. The client sends UTC bounds and a grain, gets UTC
    bucket keys back, and does all local-calendar bucketing itself -- the
    browser knows its own zone for any instant, so the server never needs one.
    """
    if grain not in GRAINS:
        grain = "minute"
    for v in (frm, to):
        if v and not _UTC.match(v):
            raise ValueError("from/to must be UTC YYYY-MM-DDTHH:MM")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rt = rates()
    where, args = ["ts!=''"], []
    if frm: where.append("ts >= ?"); args.append(frm + ":00Z")
    if to:  where.append("ts <= ?"); args.append(to + ":59Z")
    wsql = " AND ".join(where)

    span = con.execute(f"SELECT MIN(ts) a, MAX(ts) b FROM calls WHERE {wsql}", args).fetchone()

    def fetch(g):
        return con.execute(
            f"SELECT strftime(?, replace(ts,'Z','')) AS t, user, model_id,"
            f" COUNT(*) calls, SUM(in_tok) i, SUM(out_tok) o,"
            f" SUM(cache_read) cr, SUM(cache_write) cw"
            f" FROM calls WHERE {wsql} GROUP BY t, user, model_id ORDER BY t",
            [GRAINS[g]] + args).fetchall()

    raw = fetch(grain)
    if len(raw) > MAXROWS and grain == "minute":
        grain = "hour"
        raw = fetch(grain)
    con.close()

    cat, keys = catalogue(), set(rt)
    out, unpriced, listed = [], {}, set()
    for r in raw:
        key, disp = resolve(r["model_id"], cat, keys)
        p = {k: v for k, v in (rt.get(key) or {}).items() if not k.startswith("_")} if key else {}
        toks = r["i"] + r["o"] + r["cr"] + r["cw"]
        if not p and toks:
            unpriced[disp] = unpriced.get(disp, 0) + toks
        elif p and (rt.get(key) or {}).get("_source") == "list":
            listed.add(disp)
        cost = (r["i"]/1e6)*p.get("input",0) + (r["o"]/1e6)*p.get("output",0) \
             + (r["cr"]/1e6)*p.get("cache_read",0) + (r["cw"]/1e6)*p.get("cache_write",0)
        out.append({"t": r["t"], "user": r["user"], "model": disp, "calls": r["calls"],
                    "cost": round(cost, 6), "in": r["i"], "out": r["o"],
                    "cache_read": r["cr"], "cache_write": r["cw"], "priced": bool(p)})
    unp = [{"model": k, "tokens": v} for k, v in sorted(unpriced.items(), key=lambda kv: -kv[1])]
    return out, grain, {"min": span["a"], "max": span["b"]}, unp, sorted(listed)

sys.path.insert(0, HERE)
from models import catalogue, resolve

def run_pipeline():
    """(ok, message, seconds). Never raises."""
    t0 = time.time()
    env = dict(os.environ)
    try:
        # start_new_session so a timeout kills the whole pipeline, not just the
        # shell, leaving collect.py orphaned and writing to spend.db
        p = subprocess.Popen(["./refresh.sh", "--fast"], cwd=HERE, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
        try:
            o, e = p.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(p.pid), 15)
            o, e = p.communicate(timeout=10)
            return False, "pipeline timed out after 180s", round(time.time()-t0, 1)
        return p.returncode == 0, (e or o)[-400:], round(time.time()-t0, 1)
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}", round(time.time()-t0, 1)

def last_built():
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(os.path.getmtime(DB)))
    except OSError:
        return None

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw): super().__init__(*a, directory=HERE, **kw)
    def log_message(self, fmt, *args):
        if "/api/" in self.path:
            sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.rstrip("/") != "/api/data":
            return super().do_GET()
        q = parse_qs(u.query)
        one = lambda k, d=None: (q.get(k) or [d])[0]
        try:
            rows, grain, span, unpriced, listed = query(one("from"), one("to"), one("grain"))
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": str(e)})
        return self._json(200, {
            "generated": last_built(), "grain": grain, "rows": rows, "span": span,
            "users": sorted({r["user"] for r in rows}),
            "models": sorted({r["model"] for r in rows}),
            "unpriced_models": unpriced,
            "list_priced_models": listed,
        })

    def do_POST(self):
        global _last_refresh
        if urlparse(self.path).path.rstrip("/") != "/api/refresh":
            return self._json(404, {"error": "not found"})
        with _lock:
            age = time.time() - _last_refresh
            if age < MIN_GAP:
                return self._json(429, {"error": "too soon", "retry_after": int(MIN_GAP-age)+1})
            # arm the gate on the ATTEMPT, not on success: otherwise a failing
            # pipeline is retriable every 60s by every client forever
            _last_refresh = time.time()
            ok, msg, secs = run_pipeline()
        if not ok: return self._json(500, {"error": "pipeline failed", "detail": msg})
        return self._json(200, {"took": secs, "generated": last_built()})

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), Handler) as httpd:
        print(f"dashboard on http://127.0.0.1:{PORT}/", file=sys.stderr, flush=True)
        httpd.serve_forever()
