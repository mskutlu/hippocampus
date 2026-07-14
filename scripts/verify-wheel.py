from __future__ import annotations

import sys
import zipfile
from pathlib import Path


wheel = Path(sys.argv[1])
required = {
    "hippocampus/migrations/001_initial.sql",
    "hippocampus/migrations/007_integrity.sql",
    "hippocampus/migrations/008_wiki_fts.sql",
    "hippocampus/assets/hooks/session-start.sh.template",
    "hippocampus/assets/pi-extension/index.ts",
    "hippocampus/web/static/index.html",
}
with zipfile.ZipFile(wheel) as archive:
    missing = required.difference(archive.namelist())
if missing:
    raise SystemExit("missing wheel assets: " + ", ".join(sorted(missing)))
print(f"verified {wheel}")
