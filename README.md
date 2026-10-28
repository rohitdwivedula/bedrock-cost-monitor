# bedrock-cost-monitor

Per-user Amazon Bedrock spend dashboard. Reads Bedrock **model invocation logs**
from S3, attributes token usage to the IAM principal that made each call, prices
it using rates derived from your own AWS invoice, and serves a time-series
dashboard.

## Why the rates come from the invoice

`rates.py` divides billed cost by billed quantity per model per token tier from
Cost Explorer, giving dollars per million tokens as *actually charged* to your
account. That is deliberately not a published price list: Bedrock bills above
Anthropic's list rates, and your account may have its own discounts. Comparing
against a client-side token counter will differ for this reason.

## Setup

```sh
cp config.example.json config.json   # then fill in your account id and buckets
aws sts get-caller-identity          # confirm credentials work
./refresh.sh                         # first data pull
./server.py                          # http://127.0.0.1:8080/
```

Requires an AWS identity with `s3:GetObject` and `s3:ListBucket` on the
invocation-log buckets and `ce:GetCostAndUsage`. Bedrock invocation logging must
be enabled **in every region you use** — it is a per-region setting, and calls
made in an unlogged region are invisible here.

Turn **off** the text, image, embedding and video payload options when enabling
it. The dashboard needs only the token counts, and leaving payload delivery on
writes full prompts and completions into S3.

## How it fits together

| file | role |
|---|---|
| `server.py` | HTTP origin. Serves `index.html`, `GET /api/data`, `POST /api/refresh`. |
| `collect.py` | Syncs new log objects from S3 into `spend.db` (SQLite). Incremental. |
| `rates.py` | Derives per-token rates from Cost Explorer into `rates.json`. |
| `build_data.py` | Maps Bedrock model ids to Cost Explorer service names. Imported by `server.py`. |
| `dashconfig.py` | Loads `config.json`. Override the path with `DASH_CONFIG`. |
| `refresh.sh` | Runs the pipeline: rates, then collect. `--fast` skips rates. |
| `index.html` | Single-file frontend. Hand-rolled SVG, no CDN or build step. |

`GET /api/data` takes `from`, `to`, `hours`, `interval` and `tzoff`, and does the
time bucketing and pricing in SQL. The browser only filters by user, switches the
series dimension, and draws — those actions make no network call. Changing the
range or interval does.

`tzoff` is the viewer's UTC offset in minutes, so day buckets break at local
midnight rather than UTC midnight. A fixed offset mis-buckets a range that
straddles a daylight-saving transition; fixing that properly means passing the
IANA zone name and bucketing with `zoneinfo`.

## Things that will confuse you otherwise

- **`server.py` binds `127.0.0.1` on purpose.** Authentication lives upstream at
  CloudFront, not in the app. Binding `0.0.0.0` would expose per-user spend data
  and the refresh endpoint with no authentication at all.
- **`collect.py` only reads recent date prefixes.** S3 keys are laid out as
  `.../<region>/YYYY/MM/DD/HH/<object>.gz`, and the collector asks S3 only about
  today and the previous `DASH_LOOKBACK_DAYS` days. Bedrock writes roughly one
  object per call, so these buckets reach hundreds of thousands of keys and
  listing from the bucket root on every run would dominate the S3 bill.
  **Consequence:** if nothing runs for longer than that window, those days are
  never asked about again and the dashboard shows nothing for them. The data is
  still in S3 and untouched. Recover it with
  `DASH_LOOKBACK_DAYS=30 ./refresh.sh`. Nothing currently detects the gap.
- **Attribution is per IAM user**, taken from `identity.arn` in each record. Two
  access keys belonging to one user are indistinguishable.
- **A model missing from `rates.json` prices at $0.** This happens when a model is
  new and Cost Explorer has not billed it yet. Re-run `rates.py` once billing
  catches up.

## Deployment

Intended target is a small EC2 instance behind CloudFront, using an **IAM
instance role** rather than a static access key, with the security group allowing
inbound HTTP only from CloudFront's managed `origin-facing` prefix list.

`config.json`, `rates.json`, `spend.db`, `mirror/` and the deploy identifier
dotfiles are gitignored. They contain account identifiers, invoice-derived
pricing, and per-person usage.
