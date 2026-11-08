"""Save a raw response from an Ecowitt gateway so the parser can be checked against it.

    python -m ventctl.tools.capture_ecowitt 192.168.1.50 [out.json]

Then run:  python -m ventctl.tools.capture_ecowitt --parse out.json
to see what the app would read from it. Commit the capture under tests/fixtures/
to lock the format in with a test.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone

from ..hardware.ecowitt import parse_livedata
from ..hardware.http import base_url, get_json


def show(data: dict) -> None:
    for r in parse_livedata(data, datetime.now(timezone.utc)):
        print(f"{r.sensor_id:18} temp={r.temp_c!s:>6} C  rh={r.rh_pct!s:>5} %  battery_low={r.battery_low}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if args[0] == "--parse":
        show(json.load(open(args[1])))
        return
    host, out = args[0], (args[1] if len(args) > 1 else "ecowitt_livedata.json")
    data = asyncio.run(get_json(f"{base_url(host)}/get_livedata_info", 6))
    with open(out, "w") as f:
        json.dump(data, f, indent=2)
    print(f"saved {out}")
    show(data)


if __name__ == "__main__":
    main()
