import json, subprocess
svcs = ["Claude Opus 5.5 (Amazon Bedrock Edition)","Claude Fable 5.1 (Amazon Bedrock Edition)"]
for svc in svcs:
    r = subprocess.run(["aws","ce","get-cost-and-usage","--time-period","Start=2026-10-01,End=2026-10-28",
        "--granularity","MONTHLY","--metrics","UnblendedCost","UsageQuantity",
        "--filter",json.dumps({"Dimensions":{"Key":"SERVICE","Values":[svc]}}),
        "--group-by","Type=DIMENSION,Key=USAGE_TYPE","--region","us-east-1","--output","json"],
        capture_output=True,text=True)
    if r.returncode!=0: print("ERR",r.stderr[:200]); continue
    d=json.loads(r.stdout)
    print("="*90); print(svc)
    print(f"{'usd':>10s} {'qty':>16s} {'usd_per_1k':>12s}  usage_type")
    for m in d["ResultsByTime"]:
        for g in sorted(m["Groups"], key=lambda x:-float(x["Metrics"]["UnblendedCost"]["Amount"])):
            c=float(g["Metrics"]["UnblendedCost"]["Amount"]); q=float(g["Metrics"]["UsageQuantity"]["Amount"])
            rate=(c/q*1000) if q else 0
            print(f"{c:10.2f} {q:16.0f} {rate:12.6f}  {g['Keys'][0]}")
