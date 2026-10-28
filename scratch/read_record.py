import gzip, json, glob, sys
f = sorted(glob.glob("/home/ardula/bedrock-spend/s3dump/*.gz"))[-1]
raw = gzip.open(f, "rt").read()
d = json.loads(raw.splitlines()[0])
for k in ("timestamp","accountId","region","requestId","operation","modelId","inferenceRegion"):
    if k in d: print(f"{k} = {d[k]}")
print(f"identity = {d.get('identity')}")
for sec in ("input","output"):
    s = d.get(sec, {})
    print(f"{sec} fields = {[x for x in s.keys() if 'Body' not in x]}")
    for tk in [x for x in s if 'oken' in x or 'Count' in x]:
        print(f"   {sec}.{tk} = {s[tk]}")
