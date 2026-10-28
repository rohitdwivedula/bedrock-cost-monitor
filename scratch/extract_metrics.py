import json, subprocess, datetime as dt, itertools, sys, csv

METRICS = ["Invocations","InputTokenCount","OutputTokenCount",
           "CacheReadInputTokenCount","CacheWriteInputTokenCount"]
REGIONS = ["us-east-1","us-west-2","us-east-2","eu-central-1","ap-northeast-1"]
NOW   = dt.datetime.now(dt.timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
START = NOW - dt.timedelta(days=450)

def aws(*a):
    r = subprocess.run(["aws",*a],capture_output=True,text=True)
    if r.returncode != 0:
        print("ERR", " ".join(a)[:120], r.stderr[:200], file=sys.stderr); return None
    return json.loads(r.stdout) if r.stdout.strip() else {}

rows = []
for region in REGIONS:
    # discover (metric, modelId) pairs
    pairs = []
    for m in METRICS:
        d = aws("cloudwatch","list-metrics","--region",region,"--namespace","AWS/Bedrock",
                "--metric-name",m,"--output","json")
        if not d: continue
        for item in d.get("Metrics",[]):
            dims = {x["Name"]:x["Value"] for x in item.get("Dimensions",[])}
            if set(dims) == {"ModelId"}:
                pairs.append((m, dims["ModelId"]))
    pairs = sorted(set(pairs))
    print(f"{region}: {len(pairs)} (metric,model) series", file=sys.stderr)

    # chunk queries (<=500 per call) and time (<=100800 datapoints per call)
    for chunk in [pairs[i:i+100] for i in range(0,len(pairs),100)]:
        queries = [{"Id":f"q{i}",
                    "MetricStat":{"Metric":{"Namespace":"AWS/Bedrock","MetricName":m,
                                            "Dimensions":[{"Name":"ModelId","Value":mid}]},
                                  "Period":86400,"Stat":"Sum"},
                    "ReturnData":True}
                   for i,(m,mid) in enumerate(chunk)]
        idmap = {f"q{i}":(m,mid) for i,(m,mid) in enumerate(chunk)}
        token=None
        while True:
            cmd = ["cloudwatch","get-metric-data","--region",region,
                   "--start-time",START.isoformat(),"--end-time",NOW.isoformat(),
                   "--metric-data-queries",json.dumps(queries),
                   "--max-datapoints","100800","--output","json"]
            if token: cmd += ["--next-token",token]
            d = aws(*cmd)
            if not d: break
            for res in d.get("MetricDataResults",[]):
                m,mid = idmap[res["Id"]]
                for ts,val in zip(res.get("Timestamps",[]),res.get("Values",[])):
                    rows.append({"region":region,"date":ts[:10],"model_id":mid,
                                 "metric":m,"value":val})
            token = d.get("NextToken")
            if not token: break

with open("raw_metrics.csv","w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=["region","date","model_id","metric","value"])
    w.writeheader(); w.writerows(rows)
print(f"wrote {len(rows)} rows to raw_metrics.csv", file=sys.stderr)
dates=[r["date"] for r in rows]
print("date range:", min(dates) if dates else "none", "->", max(dates) if dates else "none", file=sys.stderr)
