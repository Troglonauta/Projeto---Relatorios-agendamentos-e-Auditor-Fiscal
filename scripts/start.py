"""Runner unico para dev local — sobe Web + Worker no mesmo terminal.

Uso:
    python scripts/start.py

O que faz:
1. Spawn do worker Celery (`--pool=solo`, broker auto-detectado).
2. Spawn do servidor web (via `scripts/supervisor.py` para suportar restart).
3. Prefixa cada linha com [WEB] ou [WORKER] coloridos para voce diferenciar.
4. Ctrl+C encerra os DOIS processos de forma limpa.

Cross-platform:
- Windows: usa `CREATE_NEW_PROCESS_GROUP` + `CTRL_BREAK_EVENT` para encerrar.
- POSIX:   usa `start_new_session=True` + `SIGTERM`.

Se algum dos processos morrer sozinho, o runner derruba o outro (modo "todos
ou nenhum"), facilitando diagnosticar falhas em desenvolvimento.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

IS_WINDOWS = (os.name == "nt")
ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# Forca UTF-8 na saida do parent — em Windows o default e' cp1252 que quebra
# em caracteres acentuados ou emoji.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

# Sinaliza para os subprocessos usarem stdio em UTF-8
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

# Cores ANSI — Windows 10+ tambem suporta no terminal moderno.
COLORS = {
    "WEB":    "\033[36m",   # cyan
    "WORKER": "\033[35m",   # magenta
    "OK":     "\033[32m",
    "ERR":    "\033[31m",
    "DIM":    "\033[2m",
    "RESET":  "\033[0m",
}


def _label(tag: str) -> str:
    color = COLORS.get(tag, "")
    return f"{color}[{tag:<6}]{COLORS['RESET']}"


def _spawn(cmd: list[str], cwd: Path) -> subprocess.Popen:
    """Spawn portatil com process group separado para sinal limpo."""
    if IS_WINDOWS:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        return subprocess.Popen(
            cmd, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags,
        )
    else:
        return subprocess.Popen(
            cmd, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True, encoding="utf-8", errors="replace",
            start_new_session=True,
        )


def _stream(proc: subprocess.Popen, tag: str) -> None:
    """Le stdout linha a linha e imprime com prefixo colorido."""
    prefix = _label(tag)
    for line in iter(proc.stdout.readline, ""):  # type: ignore[union-attr]
        if not line:
            break
        sys.stdout.write(f"{prefix} {line}")
        sys.stdout.flush()


def _terminate(proc: subprocess.Popen, tag: str, timeout: float = 5.0) -> None:
    """Encerra de forma graciosa: CTRL_BREAK (Win) ou SIGTERM (POSIX),
    com fallback duro depois do timeout.
    """
    if proc.poll() is not None:
        return
    print(f"{_label(tag)} {COLORS['DIM']}encerrando...{COLORS['RESET']}")
    try:
        if IS_WINDOWS:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"{_label(tag)} {COLORS['ERR']}forcando kill{COLORS['RESET']}")
        proc.kill()
        try: proc.wait(timeout=2)
        except Exception: pass


def main() -> int:
    print(f"{COLORS['OK']}=== Protheus Reports — dev runner ==={COLORS['RESET']}")
    print(f"  Python:  {PY}")
    print(f"  Projeto: {ROOT}")
    print(f"  {COLORS['DIM']}Ctrl+C para encerrar os dois processos{COLORS['RESET']}\n")

    # 1) Worker Celery
    worker_cmd = [
        PY, "-m", "celery",
        "-A", "backend.queue.celery_app", "worker",
        "--loglevel=info", "--pool=solo",
    ]
    # 2) Web via supervisor (suporta botao Reiniciar do /admin)
    web_cmd = [PY, "scripts/supervisor.py"]

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        worker = _spawn(worker_cmd, ROOT)
        procs.append(("WORKER", worker))
        print(f"{_label('WORKER')} {COLORS['OK']}PID {worker.pid} iniciado{COLORS['RESET']}")

        web = _spawn(web_cmd, ROOT)
        procs.append(("WEB", web))
        print(f"{_label('WEB')} {COLORS['OK']}PID {web.pid} iniciado{COLORS['RESET']}\n")
    except FileNotFoundError as exc:
        print(f"{COLORS['ERR']}Falha ao iniciar: {exc}{COLORS['RESET']}")
        for _, p in procs:
            _terminate(p, "?")
        return 1

    # Threads para encaminhar stdout
    threads = []
    for name, proc in procs:
        t = threading.Thread(target=_stream, args=(proc, name), daemon=True)
        t.start()
        threads.append(t)

    shutting_down = threading.Event()

    def _shutdown_all(signo=None, frame=None):
        if shutting_down.is_set():
            return
        shutting_down.set()
        print(f"\n{COLORS['DIM']}=== Ctrl+C recebido — derrubando processos ==={COLORS['RESET']}")
        # Derruba o WEB primeiro para o uvicorn dar resposta a requests em voo,
        # depois o WORKER para deixar Celery liberar o broker.
        order = ["WEB", "WORKER"]
        for tag in order:
            for name, p in procs:
                if name == tag:
                    _terminate(p, name)
        print(f"{COLORS['OK']}Encerramento concluido.{COLORS['RESET']}")

    # Ctrl+C / SIGTERM
    signal.signal(signal.SIGINT, _shutdown_all)
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, _shutdown_all)

    # Loop principal: detecta se qualquer processo morreu sozinho
    try:
        while not shutting_down.is_set():
            for name, p in procs:
                rc = p.poll()
                if rc is not None:
                    print(f"{_label(name)} {COLORS['ERR']}exit code {rc} — derrubando o resto{COLORS['RESET']}")
                    _shutdown_all()
                    return rc
            time.sleep(0.5)
    except KeyboardInterrupt:
        _shutdown_all()

    return 0


if __name__ == "__main__":
    sys.exit(main())
