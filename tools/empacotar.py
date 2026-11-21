#!/usr/bin/python3
"""Gera o instalador único e seu SHA-256, sem incluir dados da máquina."""

import base64
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = {
    "app": "app/caixa_al1182.py",
    "engine": "app/engine.py",
    "icon": "packaging/caixa-al1182.png",
    "desktop": "packaging/caixa-al1182.desktop.in",
    "service": "packaging/al1182-reconnect.service",
    "timer": "packaging/al1182-reconnect.timer",
}

data = {key: base64.b64encode((ROOT / path).read_bytes()).decode("ascii") for key, path in PAYLOAD.items()}
installer_source = (ROOT / "packaging/installer.py").read_text(encoding="utf-8")
script = (
    '#!/bin/sh\n# Instalador da Caixa AL-1182. Gere com tools/empacotar.py.\n'
    'exec /usr/bin/python3 - "$@" <<\'CAIXA_AL1182_INSTALLER\'\n'
    + installer_source.replace('import argparse\n', 'PAYLOAD = ' + repr(data) + '\n\nimport argparse\n', 1)
    + 'CAIXA_AL1182_INSTALLER\n'
)
path = ROOT / "caixa-al1182-instalar.sh"
path.write_text(script, encoding="utf-8")
path.chmod(0o755)
digest = hashlib.sha256(path.read_bytes()).hexdigest()
path.with_suffix(".sh.sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
print(path.name)
