import json, subprocess, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dashconfig import ACCOUNT_ID
def sim(actions, ctx=None):
    cmd=["aws","iam","simulate-principal-policy",
         "--policy-source-arn",f"arn:aws:iam::{ACCOUNT_ID}:user/ai-agents",
         "--action-names",*actions,"--output","json"]
    if ctx: cmd += ["--context-entries",ctx]
    r=subprocess.run(cmd,capture_output=True,text=True)
    if r.returncode: print("  ERR",r.stderr[:200]); return
    for e in json.loads(r.stdout)["EvaluationResults"]:
        src=[s.get("SourcePolicyId") for s in e.get("MatchedStatements",[])]
        print(f"  {e['EvalActionName']:58s} {e['EvalDecision']:22s} {src}")

print("=== can logging be turned off or bypassed? ===")
sim(["bedrock:PutModelInvocationLoggingConfiguration",
     "bedrock:DeleteModelInvocationLoggingConfiguration",
     "bedrock:InvokeModel",
     "bedrock:CreateModelInvocationJob",
     "bedrock:CreateProvisionedModelThroughput",
     "bedrock-agent:CreateAgent"])
print("=== InvokeModel in a region outside the lock ===")
sim(["bedrock:InvokeModel"],
    "ContextKeyName=aws:RequestedRegion,ContextKeyType=string,ContextKeyValues=eu-central-1")
print("=== InvokeModel in an allowed region ===")
sim(["bedrock:InvokeModel"],
    "ContextKeyName=aws:RequestedRegion,ContextKeyType=string,ContextKeyValues=us-west-2")
