#!/usr/bin/env python3
"""Download the official public workbooks used by the real-public query pack."""
from __future__ import annotations

import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; omegalul-excel-eval/1.0)"

FILES = {
    "worldbank-cmo-monthly.xlsx": (
        "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx"
    ),
    "ons-hi00-regions.xlsx": (
        "https://www.ons.gov.uk/file?uri=/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/datasets/headlinelabourforcesurveyindicatorsforallregionshi00/current/regionaltable1s.xlsx"
    ),
}


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        dest = ROOT / name
        print("GET", url)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as resp:
            dest.write_bytes(resp.read())
        print(" wrote", dest, dest.stat().st_size)


if __name__ == "__main__":
    main()
