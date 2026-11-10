"""Apply judge ownership and modes after the last COPY in every image recipe."""

import os
from pathlib import Path

for directory in Path("/opt/gtoc/problems").glob("*/validator"):
    for path in [directory, *directory.rglob("*")]:
        if path.is_symlink():
            raise ValueError(f"Unexpected symlink in protected judge: {path}")
        os.chown(path, 0, 1001)
        executable = path.name.endswith("_Verify") or path.name == "GTOC11Check_Linux"
        path.chmod(0o750 if path.is_dir() else 0o550 if executable else 0o440)
