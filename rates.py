"""Derive $/million-token rates per (model, tier) from Cost Explorer usage types."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python3 -I drops this
import json, subprocess, collections, re, sys, datetime as dt
from dashconfig import CE_REGION

# Cost Explorer labels token usage three different ways depending on the model
# family: snake_case (`..._cache_read_tokens_standard-Units`), CamelCase
# (`..._CacheReadInputTokenCount-Units`) and hyphenated
# (`...-DeepSeek-R1-input-tokens`). Matching only the first missed 333 of 360
# token usage types, which left whole models absent from rates.json.
#
# ORDER MATTERS: "CacheReadInputTokenCount" contains "InputToken", so the cache
# patterns must be tested before the plain input/output ones or all cache cost
# would be folded into the input tier.
TIER_PATTERNS = [
    (re.compile(r'cache[_\-]?read',   re.I), "cache_read"),
    (re.compile(r'cache[_\-]?write',  re.I), "cache_write"),
    (re.compile(r'output[_\-]?tokens?', re.I), "output"),
    (re.compile(r'input[_\-]?tokens?',  re.I), "input"),
]

def ce(start, end, svc=None, groupby="Type=DIMENSION,Key=USAGE_TYPE"):
    ops = [{"Dimensions":{"Key":"RECORD_TYPE","Values":["Usage"]}}]
    if svc: ops.append({"Dimensions":{"Key":"SERVICE","Values":[svc]}})
    flt = ops[0] if len(ops)==1 else {"And":ops}
    cmd=["aws","ce","get-cost-and-usage","--time-period",f"Start={start},End={end}",
         "--granularity","MONTHLY","--metrics","UnblendedCost","UsageQuantity",
         "--filter",json.dumps(flt),"--group-by",groupby,
         "--region",CE_REGION,"--output","json"]
    r=subprocess.run(cmd,capture_output=True,text=True)
    if r.returncode: raise SystemExit("CE error: "+r.stderr[:300])
    return json.loads(r.stdout)


# Cost Explorer can only price a model the account has actually been billed for.
# For anything else, fall back to the public Price List API. These are list
# prices, not your discounted rate, so they are tagged and CE always wins.
LIST_TIERS = {
    "input tokens":       "input",
    "output tokens":      "output",
    "cache read tokens":  "cache_read",
    "cache write tokens": "cache_write",
}

def list_prices(regions):
    """{model_name: {tier: $/MTok}} from the public price list."""
    out = {}
    for region in regions:
        p = subprocess.run(["aws","pricing","get-products","--region","us-east-1",
             "--service-code","AmazonBedrock","--max-items","1000",
             "--filters",json.dumps([{"Type":"TERM_MATCH","Field":"regionCode","Value":region}]),
             "--output","json"], capture_output=True, text=True)
        if p.returncode:
            print(f"  price list unavailable for {region}: {p.stderr[:90]}", file=sys.stderr)
            continue
        for blob in json.loads(p.stdout).get("PriceList", []):
            d = json.loads(blob)
            a = d["product"]["attributes"]
            model = a.get("model")
            # only the unmodified tiers; "flex"/"priority"/"global" variants are
            # separately priced and the invocation log cannot tell them apart
            tier = LIST_TIERS.get((a.get("inferenceType") or "").strip().lower())
            if not model or not tier:
                continue
            dim = list(d["terms"]["OnDemand"].values())[0]["priceDimensions"]
            pd = list(dim.values())[0]
            usd = float(pd["pricePerUnit"]["USD"])
            per_mtok = usd * 1000 if "1K" in pd["unit"] else usd
            out.setdefault(model, {}).setdefault(tier, round(per_mtok, 6))
    return out

def main():
    end = (dt.date.today()+dt.timedelta(days=1)).isoformat()
    start = (dt.date.today().replace(day=1)-dt.timedelta(days=120)).replace(day=1).isoformat()
    svcs = set()
    d = ce(start,end,groupby="Type=DIMENSION,Key=SERVICE")
    for m in d["ResultsByTime"]:
        for g in m["Groups"]:
            s=g["Keys"][0]
            if "(Amazon Bedrock Edition)" in s: svcs.add(s)
    rates={}
    for svc in sorted(svcs):
        d = ce(start,end,svc=svc)
        agg=collections.defaultdict(lambda:[0.0,0.0])   # tier -> [cost, qty]
        for m in d["ResultsByTime"]:
            for g in m["Groups"]:
                ut=g["Keys"][0]
                cost=float(g["Metrics"]["UnblendedCost"]["Amount"])
                qty =float(g["Metrics"]["UsageQuantity"]["Amount"])
                for pat,tier in TIER_PATTERNS:
                    if pat.search(ut):
                        a=agg[tier]; a[0]+=cost; a[1]+=qty
                        break
        model = svc.replace(" (Amazon Bedrock Edition)","")
        r={}
        for tier,(c,q) in agg.items():
            if q>0: r[tier]=round(c/q,6)      # qty unit == 1M tokens -> $/MTok
        if r: rates[model]=r
    derived = set(rates)
    # fill gaps with list prices so a model is never silently priced at $0
    regions = sorted({CE_REGION, "us-east-2", "us-west-1", "us-west-2"})
    for model, tiers in list_prices(regions).items():
        if model in rates:
            continue
        rates[model] = dict(tiers, _source="list")
    for m in derived:
        rates[m]["_source"] = "invoice"
    json.dump(rates, open("rates.json","w"), indent=2, sort_keys=True)
    n_list = sum(1 for v in rates.values() if v.get("_source") == "list")
    print(f"rates for {len(rates)} models -> rates.json "
          f"({len(derived)} from your invoice, {n_list} from the public price list)")


if __name__=="__main__": main()
