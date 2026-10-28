import gzip, json, subprocess, sys
B, = sys.argv[1:2]
ls = subprocess.run(["aws","s3","ls",f"s3://{B}/","--recursive"],capture_output=True,text=True).stdout
keys=[l.split()[-1] for l in ls.splitlines() if l.strip().endswith(".gz")]
pay=[k for k in keys if "/data/" in k]
main=[k for k in keys if "/data/" not in k]
print(f"{B}: {len(main)} log files, {len(pay)} offloaded-payload files")
if not main: return_=None; print("  (no log file yet)"); raise SystemExit
k=sorted(main)[-1]
subprocess.run(["aws","s3","cp",f"s3://{B}/{k}","/tmp/r.json.gz","--quiet"],check=True)
d=json.loads(gzip.open("/tmp/r.json.gz","rt").read().splitlines()[0])
print("  region:",d.get("region"),"| identity:",d.get("identity",{}).get("arn"))
print("  modelId:",str(d.get("modelId"))[:72])
print("  input keys :",list(d.get("input",{}).keys()))
print("  output keys:",list(d.get("output",{}).keys()))
body=[x for x in list(d.get("input",{}))+list(d.get("output",{})) if "Body" in x or "body" in x]
print("  PAYLOAD PRESENT?" , body if body else "NO - metadata only")
