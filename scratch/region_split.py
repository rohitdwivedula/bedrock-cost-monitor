import json, subprocess, collections
r = subprocess.run(["aws","ce","get-cost-and-usage",
    "--time-period","Start=2026-10-01,End=2026-10-28","--granularity","MONTHLY",
    "--metrics","UnblendedCost",
    "--filter",json.dumps({"Dimensions":{"Key":"RECORD_TYPE","Values":["Usage"]}}),
    "--group-by","Type=DIMENSION,Key=USAGE_TYPE","--region","us-east-1","--output","json"],
    capture_output=True,text=True)
d=json.loads(r.stdout)
reg=collections.Counter()
for m in d["ResultsByTime"]:
    for g in m["Groups"]:
        ut=g["Keys"][0]; v=float(g["Metrics"]["UnblendedCost"]["Amount"])
        if "_tokens" not in ut: continue
        pre = ut.split("-")[0]
        reg[{"USE1":"us-east-1","USW2":"us-west-2"}.get(pre,pre)] += v
tot=sum(reg.values())
print("October 2026 LLM token spend by region")
for k,v in reg.most_common():
    print(f"  {k:12s} {v:10.2f}  {v/tot*100:5.1f}%")
print(f"  {'TOTAL':12s} {tot:10.2f}")
