"""Prepare optional click D1 binding from config.yaml; no remote calls or secrets output.

Used only by the existing manual Worker deployment workflow. Disabling the
switch removes all click bindings and makes the /c route a silent no-op.
"""
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dealradar.config import load_config
from dealradar.clicks import endpoint, settings


def configure(path, cfg):
    options = settings(cfg)
    text = path.read_text()
    if not options["enabled"]:
        return False
    base = endpoint(cfg)
    database = os.environ.get("CLICK_SIGNAL_D1_ID", "")
    token = os.environ.get("CLICK_SIGNAL_TOKEN", "")
    if not base or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", database) or len(token) < 32:
        raise ValueError("click signal requires our cookie-free Worker endpoint, D1 ID and token (32+ characters)")
    domain = cfg.custom_domain.removeprefix("www.")
    values = {"CLICK_SIGNAL_ENABLED": "true", "CLICK_SIGNAL_ORIGINS": f"https://{domain},https://www.{domain}",
              "CLICK_SIGNAL_HOURLY_CAP": str(options["hourly_cap"]),
              "CLICK_SIGNAL_ISOLATE_PER_MINUTE": str(options["isolate_per_minute"]),
              "CLICK_SIGNAL_DEAL_PER_MINUTE": str(options["deal_per_minute"])}
    text = re.sub(r'^CLICK_SIGNAL_ENABLED = .*$', "\n".join(k + " = " + json.dumps(v) for k, v in values.items()), text, flags=re.M)
    text += ('\n[[d1_databases]]\nbinding = "CLICKS"\ndatabase_name = "wowpriced-clicks"\n'
             'database_id = ' + json.dumps(database) + '\n')
    path.write_text(text)
    return True


if __name__ == "__main__":
    try:
        active = configure(Path(sys.argv[1]), load_config())
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    # Public switch only; never print the config, database binding or secrets.
    print("true" if active else "false")
