"""Authoritative model-id -> model-name mapping, straight from Bedrock.

Replaces the string-munging + difflib guesswork that used to live in
build_data.py. That approach silently priced `Claude Sonnet 4.5` using
`Claude Sonnet 5`'s rates because the two names are 96% similar. There is no
fuzzy matching here by design: a model we cannot resolve is reported as
unpriced rather than charged at a neighbour's rate.

`bedrock:ListFoundationModels` gives modelId -> (providerName, modelName).
Cost Explorer names the same model `"<modelName> (Amazon Bedrock Edition)"`,
or `"<providerName> <modelName> ..."` for non-Anthropic providers, so the join
is a deterministic suffix rather than a similarity score.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python3 -I drops this

import json, os, re, subprocess, time

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "models.json")
MAX_AGE = 7 * 24 * 3600          # refresh the catalogue weekly

def _fetch(regions):
    out = {}
    for r in regions:
        p = subprocess.run(["aws", "bedrock", "list-foundation-models",
                            "--region", r, "--output", "json"],
                           capture_output=True, text=True)
        if p.returncode:
            continue
        for m in json.loads(p.stdout).get("modelSummaries", []):
            out.setdefault(m["modelId"],
                           [m.get("providerName", ""), m.get("modelName", "")])
    return out

def catalogue(regions=None, force=False):
    """modelId -> [providerName, modelName], cached on disk."""
    if not force and os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < MAX_AGE:
        try:
            with open(CACHE) as f:
                return json.load(f)
        except Exception:
            pass
    if regions is None:
        from dashconfig import CE_REGION
        regions = [CE_REGION, "us-east-2", "us-west-1", "us-west-2"]
    got = _fetch(regions)
    if got:
        with open(CACHE, "w") as f:
            json.dump(got, f, indent=1, sort_keys=True)
        return got
    try:                                   # network failed; fall back to stale cache
        with open(CACHE) as f:
            return json.load(f)
    except Exception:
        return {}

_ROUTING = re.compile(r'^(us|eu|apac|global|us-gov)\.')
_SUFFIX  = re.compile(r'(-\d{8})?(-v\d+(:\d+)?)?(:[0-9a-z]+)?$')

def _stem(mid):
    return _SUFFIX.sub('', _ROUTING.sub('', mid.split('/')[-1]))

def resolve(model_id, cat=None, rate_keys=None):
    """(rate_key | None, display_name).

    rate_key is the Cost Explorer service name with the "(Amazon Bedrock
    Edition)" suffix removed, i.e. a key of rates.json. None means we could
    not price it, and the caller must surface that rather than charge $0.
    """
    cat = catalogue() if cat is None else cat
    raw = model_id.split('/')[-1]
    base = _ROUTING.sub('', raw)
    hit = cat.get(base)
    if hit is None:
        stem = _stem(raw)
        for k, v in cat.items():
            if _stem(k) == stem:
                hit = v
                break
    if hit is None:
        return None, raw
    provider, name = hit
    # Cost Explorer sometimes names the service with the provider prefixed
    # ("OpenAI GPT-6.1 Sol") and sometimes not ("Claude Opus 5.5"), so try
    # both forms as rate keys. The display name is always the bare modelName.
    candidates = [name] + ([f"{provider} {name}"] if provider else [])
    display = name
    if rate_keys is not None:
        for c in candidates:
            if c in rate_keys:
                return c, c
        # tolerate hyphen/space drift between the catalogue and Cost Explorer
        norm = lambda s: re.sub(r'[^a-z0-9.]', '', s.lower())
        want = {norm(c) for c in candidates}
        for k in rate_keys:
            if norm(k) in want:
                return k, k
        return None, display
    return display, display

if __name__ == "__main__":
    cat = catalogue(force="--refresh" in _sys.argv)
    print(f"{len(cat)} models in catalogue -> {CACHE}")
