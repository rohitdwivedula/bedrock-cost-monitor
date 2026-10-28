import json
d=json.load(open("cur.json"))["PolicyDocument"]
sts=d["Statement"]
have={s.get("Sid") for s in sts}
if "DenyNonTokenBilledCapacity" not in have:
    sts.append({"Sid":"DenyNonTokenBilledCapacity","Effect":"Deny","Action":[
        "bedrock:CreateProvisionedModelThroughput","bedrock:UpdateProvisionedModelThroughput",
        "bedrock:CreateModelCustomizationJob","bedrock:CreateCustomModelDeployment",
        "bedrock:CreateModelImportJob","bedrock:CreateModelCopyJob",
        "bedrock:CreateMarketplaceModelEndpoint","bedrock:UpdateMarketplaceModelEndpoint",
        "bedrock:RegisterMarketplaceModelEndpoint","bedrock:CreateEvaluationJob"],"Resource":"*"})
if "DenyBatchInference" not in have:
    sts.append({"Sid":"DenyBatchInference","Effect":"Deny",
                "Action":["bedrock:CreateModelInvocationJob"],"Resource":"*"})
out=json.dumps(d,indent=2)
open("merged_policy.json","w").write(out)
print(out)
