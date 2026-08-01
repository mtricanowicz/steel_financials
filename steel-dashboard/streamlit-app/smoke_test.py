"""Headless smoke test: run each Streamlit page and assert no exceptions.

Uses Streamlit's AppTest harness so pages execute end to end against the sample
data without launching a browser or server.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parents[1] / "streamlit-app"
PAGES = [
    APP_DIR / "views" / "comparisons.py",
    APP_DIR / "views" / "latest_results.py",
    APP_DIR / "views" / "share_repurchases.py",
    APP_DIR / "views" / "insights.py",
]


def main() -> int:
    import sys

    sys.path.insert(0, str(APP_DIR))
    failures = []
    for page in PAGES:
        at = AppTest.from_file(str(page), default_timeout=30).run()
        if at.exception:
            failures.append((page.name, [e.message for e in at.exception]))
            print(f"FAIL {page.name}: {[e.message for e in at.exception]}")
        else:
            print(f"OK   {page.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
