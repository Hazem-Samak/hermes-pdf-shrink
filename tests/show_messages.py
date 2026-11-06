"""Print the exact JSON the model sees, for three demo cases.

Used to sanity-check the wording before recording the demo video.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "pdf_shrink_plugin", ROOT / "__init__.py",
    submodule_search_locations=[str(ROOT)],
)
plugin = importlib.util.module_from_spec(spec)
sys.modules["pdf_shrink_plugin"] = plugin
spec.loader.exec_module(plugin)

DEMO = ROOT / "demo"


def show(title: str, raw: str) -> None:
    payload = json.loads(raw)
    print(f"\n=== {title} ===")
    print("status :", payload.get("status"))
    print("message:", payload.get("message"))
    for key in ("saved", "quality", "smallest_safe", "suggestions", "verdict", "advice"):
        if payload.get(key):
            print(f"{key:10}:", json.dumps(payload[key], ensure_ascii=False))


if __name__ == "__main__":
    for name in ("demo-out-2mb.pdf", "demo-out-floor.pdf"):
        try:
            (DEMO / name).unlink()
        except FileNotFoundError:
            pass
    show("inspect · scan", plugin.tools.pdf_inspect({"path": str(DEMO / "scanned-report.pdf")}))
    show(
        "shrink · scan to 2 MB",
        plugin.tools.pdf_shrink(
            {"path": str(DEMO / "scanned-report.pdf"), "target_size": "2 MB",
             "output_path": str(DEMO / "demo-out-2mb.pdf")}
        ),
    )
    show(
        "shrink · scan to 500 KB (impossible)",
        plugin.tools.pdf_shrink(
            {"path": str(DEMO / "scanned-report.pdf"), "target_size": "500 KB",
             "output_path": str(DEMO / "demo-out-floor.pdf")}
        ),
    )
    show(
        "inspect · vector",
        plugin.tools.pdf_inspect({"path": str(DEMO / "vector-heavy.pdf")}),
    )
