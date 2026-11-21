"""Instalação no escopo do usuário. O empacotador insere PAYLOAD neste script."""

import argparse
import base64
import contextlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

NAME = "caixa-al1182-linux"
APP_ID = "io.github.rafaelmendoncadev.CaixaAL1182"
UNIT = "al1182-reconnect.service"
TIMER = "al1182-reconnect.timer"

HOME = Path.home()


def xdg(variable, fallback):
    value = Path(os.environ.get(variable, ""))
    return value if value.is_absolute() else HOME / fallback


DATA = xdg("XDG_DATA_HOME", ".local/share")
CONFIG = xdg("XDG_CONFIG_HOME", ".config")
STATE = xdg("XDG_STATE_HOME", ".local/state")
ROOT = DATA / NAME
RECORD = ROOT / "instalacao.json"
IDENTITY = STATE / NAME / "dispositivo"
USER_UNITS = CONFIG / "systemd/user"


class InstallError(Exception):
    pass


def command(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def systemctl(*args, check=True):
    return command("systemctl", "--user", *args, check=check)


def unit_property(unit, name):
    return systemctl("show", "--value", "-p", name, unit, check=False).stdout.strip()


def save(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(data)
            handle.close()
            staged.chmod(mode)
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def unpack(name):
    return base64.b64decode(PAYLOAD[name])


def desktop_arg(value):
    value = str(value).replace("%", "%%")
    value = "".join("\\" + char if char in '\\"$`' else char for char in value)
    return '"' + value.replace("\\", "\\\\") + '"'


def systemd_arg(value):
    return json.dumps(str(value).replace("%", "%%"), ensure_ascii=False)


def current_record():
    if not RECORD.exists():
        return None
    return json.loads(RECORD.read_text(encoding="utf-8"))


def desktop_dir():
    if not shutil.which("xdg-user-dir"):
        return None
    path = Path(command("xdg-user-dir", "DESKTOP").stdout.strip())
    if path.is_absolute() and path.is_dir() and path != HOME:
        return path
    return None


def owned_paths(record):
    return [Path(value) for value in record["files"].values() if value]


def new_record():
    paths = {
        "app": ROOT / "caixa_al1182.py",
        "engine": ROOT / "engine.py",
        "icon": DATA / "icons/hicolor/256x256/apps" / (APP_ID + ".png"),
        "menu": DATA / "applications" / (APP_ID + ".desktop"),
        "dropin": USER_UNITS / (UNIT + ".d") / "90-caixa-al1182-linux.conf",
        "service": USER_UNITS / UNIT if not unit_property(UNIT, "FragmentPath") else None,
        "timer": USER_UNITS / TIMER if not unit_property(TIMER, "FragmentPath") else None,
    }
    desktop = desktop_dir()
    paths["shortcut"] = desktop / (APP_ID + ".desktop") if desktop else None
    original = unit_property(TIMER, "UnitFileState") if paths["timer"] is None else "absent"
    if original not in ("absent", "enabled", "disabled"):
        raise InstallError("O timer existente tem um estado incompatível com a migração automática.")
    record = {
        "files": {key: str(path) if path else None for key, path in paths.items()},
        "identity": str(IDENTITY),
        "timer_state": original,
        "timer_active": systemctl("is-active", "--quiet", TIMER, check=False).returncode == 0,
    }
    if ROOT.exists() or ROOT.is_symlink():
        raise InstallError("Já existe uma pasta de dados do aplicativo sem registro de instalação.")
    if any(path.exists() or path.is_symlink() for path in owned_paths(record)):
        raise InstallError("Um arquivo de destino já existe. A instalação foi interrompida para preservá-lo.")
    if any("\n" in str(path) or "\r" in str(path) for path in [IDENTITY, *owned_paths(record)]):
        raise InstallError("Um caminho de instalação contém uma quebra de linha.")
    return record


def trust_shortcut(path):
    if not shutil.which("gio"):
        return False
    result = command("gio", "set", "-t", "string", str(path), "metadata::trusted", "true", check=False)
    return result.returncode == 0


def install_files(record):
    paths = {key: Path(value) if value else None for key, value in record["files"].items()}
    app, identity = paths["app"], Path(record["identity"])
    args = (app, "--identidade", identity)
    desktop_exec = "/usr/bin/python3 " + " ".join(map(desktop_arg, (*args, "--icone", paths["icon"])))
    desktop_icon = str(paths["icon"]).replace("\\", "\\\\").replace("\t", "\\t")
    desktop_entry = (
        unpack("desktop").decode()
        .replace("\nExec=@EXEC@\n", f"\nExec={desktop_exec}\n")
        .replace("\nIcon=@ICON@\n", f"\nIcon={desktop_icon}\n")
        .encode()
    )
    service_exec = ":/usr/bin/python3 " + " ".join(map(systemd_arg, (*args, "--automatico")))
    dropin = f"[Service]\nExecStart=\nExecStart={service_exec}\n".encode()

    save(paths["app"], unpack("app"))
    save(paths["engine"], unpack("engine"))
    save(paths["icon"], unpack("icon"))
    save(paths["menu"], desktop_entry)
    if shutil.which("desktop-file-validate"):
        command("desktop-file-validate", str(paths["menu"]))
    if paths["service"]:
        save(paths["service"], unpack("service"))
    if paths["timer"]:
        save(paths["timer"], unpack("timer"))
    save(paths["dropin"], dropin)

    if paths["shortcut"]:
        save(paths["shortcut"], desktop_entry, mode=0o755)
        if not trust_shortcut(paths["shortcut"]):
            print("Atalho criado. Se o ambiente pedir, marque-o como confiável pelo gerenciador de arquivos.")
        print(f"Atalho: {paths['shortcut']}")
    else:
        print("Pasta de área de trabalho indisponível. Abra Caixa AL-1182 pelo menu de aplicativos.")


def dependencies():
    if os.geteuid() == 0:
        raise InstallError("Execute como seu usuário comum, sem sudo.")
    needed = ("pactl", "paplay", "pw-dump", "pw-cli", "systemctl")
    missing = [name for name in needed if not shutil.which(name)]
    if missing:
        raise InstallError("Programas ausentes: " + ", ".join(missing))
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        assert Gtk.get_major_version() == 3
    except (ImportError, ValueError) as exc:
        raise InstallError("Instale python3-gi e o GTK 3 antes de continuar.") from exc


def remove_paths(record):
    # Preserve diretórios compartilhados com outras unidades ou aplicativos.
    for path in owned_paths(record):
        path.unlink(missing_ok=True)
    dropin_directory = Path(record["files"]["dropin"]).parent
    cache = ROOT / "__pycache__"
    if cache.is_dir():
        for prefix in ("caixa_al1182.", "engine."):
            for file in cache.glob(prefix + "*.pyc"):
                file.unlink()
    for directory in (dropin_directory, cache):
        with contextlib.suppress(OSError):
            directory.rmdir()


def restore(record):
    systemctl("stop", TIMER, UNIT, check=False)
    if record["timer_state"] in ("absent", "disabled"):
        systemctl("disable", TIMER, check=False)
    remove_paths(record)
    systemctl("daemon-reload")
    if record["timer_active"]:
        systemctl("start", TIMER)


def install():
    dependencies()
    record = current_record()
    first_install = record is None
    if first_install:
        record = new_record()
        save(RECORD, (json.dumps(record, ensure_ascii=False) + "\n").encode(), mode=0o600)
    try:
        if not record["files"]["timer"] or unit_property(TIMER, "FragmentPath"):
            systemctl("stop", TIMER)
        if not record["files"]["service"] or unit_property(UNIT, "FragmentPath"):
            systemctl("stop", UNIT)
        install_files(record)
        systemctl("daemon-reload")
        effective_command = unit_property(UNIT, "ExecStart")
        if record["files"]["app"] not in effective_command or "--automatico" not in effective_command:
            raise InstallError("Outra configuração do serviço está substituindo o comando do aplicativo.")
        if record["timer_state"] != "enabled":
            systemctl("enable", TIMER)
        systemctl("start", TIMER)
        if systemctl("is-enabled", "--quiet", TIMER, check=False).returncode:
            raise InstallError("O timer não ficou habilitado para as próximas sessões.")
        systemctl("is-active", "--quiet", TIMER)
    except (OSError, InstallError, subprocess.SubprocessError):
        if first_install:
            try:
                restore(record)
            except (OSError, subprocess.SubprocessError):
                print("A restauração ficou pendente. Use --desinstalar ou execute o instalador novamente.",
                      file=sys.stderr)
            else:
                RECORD.unlink(missing_ok=True)
                with contextlib.suppress(OSError):
                    ROOT.rmdir()
        else:
            systemctl("daemon-reload", check=False)
            systemctl("start", TIMER, check=False)
        raise
    print("Caixa AL-1182 instalada. A verificação automática está ativa.")


def uninstall():
    record = current_record()
    if not record:
        print("Nenhuma instalação deste aplicativo foi encontrada.")
        return
    restore(record)
    identity = Path(record["identity"])
    identity.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        identity.parent.rmdir()
    RECORD.unlink()
    with contextlib.suppress(OSError):
        ROOT.rmdir()
    print("Caixa AL-1182 desinstalada. A configuração anterior do timer foi preservada.")


def main():
    parser = argparse.ArgumentParser(description="Instalador da Caixa AL-1182")
    parser.add_argument("--desinstalar", action="store_true", help="remover a instalação deste usuário")
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.exit(1, "Execute como seu usuário comum, sem sudo.\n")
    if systemctl("show-environment", check=False).returncode:
        parser.exit(1, "O systemd de usuário está indisponível. Execute em uma sessão Linux com systemd.\n")
    runtime = xdg("XDG_RUNTIME_DIR", ".cache") / NAME
    try:
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (runtime / "instalador.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            (uninstall if args.desinstalar else install)()
    except BlockingIOError:
        parser.exit(1, "Outra instalação ou desinstalação está em andamento.\n")
    except subprocess.CalledProcessError as exc:
        error = exc.stderr.strip() or exc.stdout.strip() or "comando sem resposta"
        parser.exit(1, f"Falha ao executar {exc.cmd[0]}: {error}\n")
    except (OSError, ValueError, InstallError) as exc:
        parser.exit(1, f"Operação incompleta: {exc}\n")


if __name__ == "__main__":
    main()
