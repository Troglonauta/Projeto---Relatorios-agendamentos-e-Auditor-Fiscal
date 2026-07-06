"""Watchdog dev para suportar o botao 'Reiniciar' do admin.

Como rodar (em vez de `python run.py`):
    python scripts/supervisor.py

Comportamento:
- Sobe `python run.py` como subprocess.
- Se ele sair com exit code 3, re-spawn (ate MAX_RESTARTS no intervalo).
- Outros exit codes encerram o supervisor.

Em LXC producao, NAO use este script — o systemd com `Restart=always` (ver
`docs/DEPLOY_LXC.md`) ja faz o mesmo trabalho de forma mais robusta.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

EXIT_RESTART = 3
MAX_RESTARTS_PER_HOUR = 20
PROJECT_ROOT = Path(__file__).resolve().parent.parent

PY = sys.executable
RUN_PY = str(PROJECT_ROOT / "run.py")


def main() -> int:
    restarts = []
    while True:
        print(f"[supervisor] iniciando {PY} {RUN_PY}")
        rc = subprocess.call([PY, RUN_PY], cwd=str(PROJECT_ROOT))
        if rc != EXIT_RESTART:
            print(f"[supervisor] processo encerrou (rc={rc}) — saindo do supervisor")
            return rc

        # rate-limit: se 20+ restarts numa hora, aborta para nao virar loop infinito
        now = time.time()
        restarts = [t for t in restarts if t > now - 3600]
        restarts.append(now)
        if len(restarts) >= MAX_RESTARTS_PER_HOUR:
            print(f"[supervisor] {len(restarts)} restarts na ultima hora — abortando")
            return 1

        print(f"[supervisor] exit 3 detectado — restart em 1s (restart #{len(restarts)})")
        time.sleep(1.0)


if __name__ == "__main__":
    sys.exit(main())
