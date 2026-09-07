"""One bounded Finder request, using only the existing server-side secret."""
import json
import os
from pathlib import Path

import requests


def main():
    selection = json.loads(Path("keepa-finder-selection.json").read_text(encoding="utf-8"))
    if selection.get("perPage") != 100 or selection.get("page") != 0:
        raise ValueError("Probe must request exactly one first page of up to 100 ASINs")
    key = os.environ["KEEPA_API_KEY"]
    response = requests.post("https://api.keepa.com/query", params={"key": key, "domain": 1}, json=selection, timeout=(10, 90))
    payload = response.json()
    output = {field: payload.get(field) for field in ("asinList", "totalResults", "tokensConsumed", "tokensLeft", "refillRate", "refillIn")}
    output["http_status"] = response.status_code
    # No raw response/error/request is published: these may contain sensitive URLs.
    Path("finder-output").mkdir(exist_ok=True)
    Path("finder-output/result.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    if response.status_code != 200 or payload.get("error") or not isinstance(payload.get("asinList"), list):
        raise RuntimeError("Finder request failed; inspect safe HTTP status and token telemetry")
    Path("finder-output/asins.txt").write_text("\n".join(payload["asinList"]) + "\n", encoding="utf-8")
    print(json.dumps({"returned": len(payload["asinList"]), "total_matches": payload.get("totalResults"),
                      "tokens_used": payload.get("tokensConsumed"), "tokens_left": payload.get("tokensLeft")}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Finder probe stopped ({type(error).__name__}); request details omitted.")
        raise SystemExit(1)
