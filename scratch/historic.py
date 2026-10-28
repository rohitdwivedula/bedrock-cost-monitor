import json, subprocess, collections
def ce(**kw):
    cmd=["aws","ce","get-cost-and-usage","--time-period","Start=2025-11-01,End=2026-10-29",
         "--granularity","MONTHLY","--metrics","UnblendedCost","UsageQuantity",
         "--filter",json.dumps({"Dimensions":{"Key":"RECORD_TYPE","Values":["Usage"]}}),
         "--region","us-east-1","--output","json"]
    for k,v in kw.items(): cmd += [k,v]
    r=subprocess.run(cmd,capture_output=True,text=True)
    if r.returncode: raise SystemExit("ERR "+r.stderr[:300])
    return json.loads(r.stdout)

d = ce(**{"--group-by":"Type=DIMENSION,Key=REGION"})
# second group-by must be passed together; redo with both
cmd=["aws","ce","get-cost-and-usage","--time-period","Start=2025-11-01,End=2026-10-29",
     "--granularity","MONTHLY","--metrics","UnblendedCost",
     "--filter",json.dumps({"Dimensions":{"Key":"RECORD_TYPE","Values":["Usage"]}}),
     "--group-by","Type=DIMENSION,Key=REGION","Type=DIMENSION,Key=SERVICE",
     "--region","us-east-1","--output","json"]
d=json.loads(subprocess.run(cmd,capture_output=True,text=True).stdout)

LLM=lambda s: any(t in s for t in ("Bedrock","Claude","GPT","Nova","Titan","Llama","Mistral"))
reg=collections.Counter(); mon=collections.defaultdict(collections.Counter); mdl=collections.Counter()
for m in d["ResultsByTime"]:
    mo=m["TimePeriod"]["Start"][:7]
    for g in m["Groups"]:
        r_,s = g["Keys"]; v=float(g["Metrics"]["UnblendedCost"]["Amount"])
        if not LLM(s) or abs(v)<0.005: continue
        reg[r_]+=v; mon[mo][r_]+=v
        mdl[s.replace(" (Amazon Bedrock Edition)","")]+=v

print("=== LLM USAGE BY REGION, Nov 2025 - Oct 2026 (gross, pre-credit) ===")
tot=sum(reg.values())
for k,v in reg.most_common(): print(f"  {k:16s} {v:11.2f}  {v/tot*100:5.1f}%")
print(f"  {'TOTAL':16s} {tot:11.2f}")

print("\n=== BY MONTH x REGION ===")
regs=[r for r,_ in reg.most_common()]
print("  month    " + "".join(f"{r:>14s}" for r in regs))
for mo in sorted(mon):
    print(f"  {mo:9s}" + "".join(f"{mon[mo].get(r,0):14.2f}" for r in regs))

print("\n=== BY MODEL ===")
for k,v in mdl.most_common(): print(f"  {v:11.2f}  {k}")
