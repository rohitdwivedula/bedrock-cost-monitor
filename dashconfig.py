"""Single source of deployment-specific values.

Nothing environment-specific belongs in the other modules, so the code can be
published or moved between accounts without edits. Override the file location
with DASH_CONFIG.
"""
import json, os

_PATH = os.environ.get("DASH_CONFIG",
                       os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))

def load():
    with open(_PATH) as f:
        return json.load(f)

CFG = load()
ACCOUNT_ID    = CFG["account_id"]
CE_REGION     = CFG.get("ce_region", "us-east-1")
LOG_BUCKETS   = CFG["log_buckets"]
DEPLOY_BUCKET = CFG.get("deploy_bucket", "")
DEPLOY_PREFIX = CFG.get("deploy_prefix", "deploy")
