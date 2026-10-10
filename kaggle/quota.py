"""Print the model proxy quota of the logged-in Kaggle account.

Kaggle CLI 2.2.4 has no `kaggle benchmarks quota`; the SDK it ships has the
call. Run it with the interpreter the CLI itself uses:

    "$(head -1 "$(command -v kaggle)" | sed 's/^#!//')" kaggle/quota.py

The quota is in US dollars. `used` includes the reservations of calls that
are in flight, so it can go down again when they end.
"""

import json
from datetime import datetime, timezone

from kaggle.api.kaggle_api_extended import KaggleApi


def main() -> None:
    api = KaggleApi()
    api.authenticate()
    with api.build_kaggle_client() as client:
        reply = client.models.model_proxy_api_client.get_model_proxy_quotas()
    out = {"read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "quotas": []}
    for balance in reply.quota_balances or []:
        out["quotas"].append({
            "period": str(balance.refill_period).rsplit(".", 1)[-1].lower(),
            "used_usd": round(balance.quota_used, 4),
            "allowed_usd": balance.total_quota_allowed,
            "refill_time_utc": balance.refill_time.isoformat() if balance.refill_time else None,
        })
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
