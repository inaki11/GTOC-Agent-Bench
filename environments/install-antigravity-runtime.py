"""Include the same pinned SDK's native ARM harness for ARM Docker hosts.

The science environment/validator remain amd64. The SDK's static ARM executable
can run natively on ARM kernels, avoiding a crash in amd64 CPU emulation.
"""

import hashlib
import io
import os
import zipfile
from pathlib import Path
from urllib.request import urlopen

URL = "https://files.pythonhosted.org/packages/55/74/fe20c9b0301c9fd3fdaf4f4f92ed43fd3f1ab59c7c6b64ebbc793ec04578/google_antigravity-0.1.23-py3-none-manylinux_2_17_aarch64.musllinux_1_1_aarch64.whl"
SHA256 = "17b2d26575ef168bb3ccd74ddf39ebfe4c528ea426e4bedee8621ea7a41cf527"

with urlopen(URL, timeout=120) as response:
    archive = response.read(100 * 1024**2)
if hashlib.sha256(archive).hexdigest() != SHA256:
    raise ValueError("Antigravity ARM wheel checksum mismatch")
with zipfile.ZipFile(io.BytesIO(archive)) as wheel:
    data = wheel.read("google/antigravity/bin/localharness")
target = Path("/opt/antigravity-runtime/linux-aarch64/localharness")
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(data)
os.chmod(target, 0o755)
