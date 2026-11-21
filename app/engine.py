"""Motor compartilhado entre a interface e o timer de usuário."""

import contextlib
import fcntl
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass

from gi.repository import Gio, GLib

NAME = "AL-1182"
BLUEZ = "org.bluez"
ADAPTER = BLUEZ + ".Adapter1"
DEVICE = BLUEZ + ".Device1"
A2DP = "0000110b-0000-1000-8000-00805f9b34fb"
PROPERTIES = "org.freedesktop.DBus.Properties"
AGENT_MANAGER = BLUEZ + ".AgentManager1"
AGENT_PATH = "/io/github/rafaelmendoncadev/CaixaAL1182/Agent"
ADDRESS = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}\Z")

AGENT_XML = """
<node><interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="Cancel"/>
  <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
  <method name="RequestConfirmation">
    <arg type="o" direction="in"/><arg type="u" direction="in"/>
  </method>
  <method name="AuthorizeService">
    <arg type="o" direction="in"/><arg type="s" direction="in"/>
  </method>
</interface></node>
"""


class Problem(Exception):
    """Mensagem que pode ser exibida sem expor detalhes da máquina."""


@dataclass(frozen=True)
class Result:
    title: str
    detail: str
    kind: str = "info"
    has_sink: bool = False


@dataclass(frozen=True)
class Target:
    path: str
    info: dict

    @property
    def address(self):
        return self.info["Address"].upper()


def run(*args, timeout=4, data=None):
    return subprocess.run(
        args, input=data, capture_output=True, check=True, timeout=timeout,
        env=dict(os.environ, LC_ALL="C"),
    ).stdout


def listing(executable, *args):
    records = json.loads(run(executable, *args))
    if not isinstance(records, list):
        raise ValueError("Lista de áudio inválida")
    return records


def pulse_list(resource):
    return listing("pactl", "--format=json", "list", resource)


def playback_enabled(record):
    return record.get("mute") is False and any(
        channel.get("value", 0) > 0 for channel in record.get("volume", {}).values()
    )


def default_sink():
    return run("pactl", "get-default-sink").decode().strip()


def speaker_sink(sinks, target):
    for sink in sinks:
        props = sink.get("properties", {})
        if (
            props.get("api.bluez5.address", "").upper() == target.address
            and is_a2dp(props.get("api.bluez5.profile", ""))
        ):
            return sink
    return None


def is_a2dp(profile):
    return profile == "a2dp-sink" or profile.startswith("a2dp-sink-")


def chatgpt_stream(stream, sink):
    props = stream.get("properties", {})
    return (
        props.get("application.process.binary") == "ChatGPT"
        and stream.get("sink") == sink["index"]
        and stream.get("corked") is False
        and playback_enabled(stream)
    )


def stream_identity(stream):
    props = stream["properties"]
    node_id = int(props.get("object.id", "0"))
    serial = int(props.get("object.serial", "0"))
    pid = int(props.get("application.process.id", "0"))
    if node_id > 0 and serial > 0 and pid > 0 and serial == stream.get("index"):
        return node_id, serial, pid
    return None


def zero_chatgpt_gain(node_id, serial, pid):
    records = listing("pw-dump", "--no-colors", str(node_id))
    node = next((item for item in records if item.get("id") == node_id), {})
    info = node.get("info", {})
    props = info.get("props", {})
    if (
        node.get("type") != "PipeWire:Interface:Node"
        or props.get("media.class") != "Stream/Output/Audio"
        or props.get("application.process.binary") != "ChatGPT"
        or str(props.get("application.process.id")) != str(pid)
        or str(props.get("object.serial")) != str(serial)
    ):
        return False

    params = info.get("params", {}).get("Props", [])
    return (
        bool(params)
        and all(item.get("mute") is False and not item.get("softMute") for item in params)
        and any(
            type(item.get("volume")) in (int, float) and item["volume"] == 0
            for item in params
        )
        and all(
            "channelVolumes" not in item or any(level > 0 for level in item["channelVolumes"])
            for item in params
        )
    )


def fix_chatgpt(sink):
    """Corrige apenas o ganho oculto do fluxo atual, após cruzar Pulse e PipeWire."""
    fixed = False
    if not playback_enabled(sink):
        return fixed
    for stream in pulse_list("sink-inputs"):
        if not chatgpt_stream(stream, sink):
            continue
        identity = stream_identity(stream)
        if not identity:
            continue
        # O fluxo pode ter fechado ou sido silenciado enquanto consultávamos o node.
        live = next(
            (item for item in pulse_list("sink-inputs") if item.get("index") == stream["index"]),
            None,
        )
        if (
            live
            and chatgpt_stream(live, sink)
            and stream_identity(live) == identity
            and zero_chatgpt_gain(*identity)
        ):
            run("pw-cli", "set-param", str(identity[0]), "Props", "{ volume: 1.0 }")
            fixed = not zero_chatgpt_gain(*identity) or fixed
    return fixed


class Bluetooth:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)

    def call(self, path, interface, method, params=None, timeout=3):
        return self.bus.call_sync(
            BLUEZ, path, interface, method, params, None, Gio.DBusCallFlags.NONE,
            int(timeout * 1000), None,
        )

    def objects(self):
        try:
            return self.call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects").unpack()[0]
        except GLib.Error as exc:
            raise Problem("O serviço Bluetooth está indisponível. Verifique o Bluetooth do sistema.") from exc

    def property(self, target, name):
        return self.call(
            target.path, PROPERTIES, "Get", GLib.Variant("(ss)", (DEVICE, name)),
        ).unpack()[0]

    def trust(self, target):
        self.call(
            target.path, PROPERTIES, "Set",
            GLib.Variant("(ssv)", (DEVICE, "Trusted", GLib.Variant("b", True))),
        )

    def connect_audio(self, target):
        with contextlib.suppress(GLib.Error):
            self.call(target.path, DEVICE, "ConnectProfile", GLib.Variant("(s)", (A2DP,)), timeout=10)

    def disconnect(self, target):
        with contextlib.suppress(GLib.Error):
            self.call(target.path, DEVICE, "Disconnect", timeout=5)

    def discover(self, adapters):
        started = []
        try:
            for adapter in adapters:
                with contextlib.suppress(GLib.Error):
                    self.call(adapter, ADAPTER, "StartDiscovery")
                    started.append(adapter)
            if not started:
                raise Problem("A busca Bluetooth não pôde começar. Confira o adaptador e tente novamente.")
            time.sleep(10)
            return self.objects()
        finally:
            for adapter in started:
                with contextlib.suppress(GLib.Error):
                    self.call(adapter, ADAPTER, "StopDiscovery")

    def pair(self, target):
        # Um agente do próprio aplicativo, limitado à caixa desta tentativa.
        owner = self.bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "GetNameOwner", GLib.Variant("(s)", (BLUEZ,)), None, Gio.DBusCallFlags.NONE, 3000, None,
        ).unpack()[0]

        def handle_agent(_bus, sender, _path, _interface, method, params, invocation):
            args = params.unpack()
            own_request = sender == owner and (
                method in ("Release", "Cancel") or (args and args[0] == target.path)
            )
            if own_request and (method != "AuthorizeService" or args[1] == A2DP):
                invocation.return_value(None)
            else:
                invocation.return_dbus_error("org.bluez.Error.Rejected", "Dispositivo fora da tentativa")

        context = GLib.MainContext.new()
        context.push_thread_default()
        loop = GLib.MainLoop.new(context, False)
        errors = []

        def paired(connection, async_result):
            try:
                connection.call_finish(async_result)
            except GLib.Error as exc:
                errors.append(exc)
            finally:
                loop.quit()

        registration = 0
        agent_registered = False
        try:
            interface = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
            registration = self.bus.register_object(AGENT_PATH, interface, handle_agent, None, None)
            agent = GLib.Variant("(o)", (AGENT_PATH,))
            self.call(
                "/org/bluez", AGENT_MANAGER, "RegisterAgent",
                GLib.Variant("(os)", (AGENT_PATH, "NoInputNoOutput")),
            )
            agent_registered = True
            if self.property(target, "Paired"):
                return
            self.bus.call(
                BLUEZ, target.path, DEVICE, "Pair", None, None,
                Gio.DBusCallFlags.NONE, 15000, None, paired,
            )
            loop.run()
            if not self.property(target, "Paired"):
                if errors:
                    raise errors[0]
                raise Problem("O Bluetooth não confirmou o pareamento da caixa.")
        finally:
            if agent_registered:
                if errors:
                    with contextlib.suppress(GLib.Error):
                        self.call(target.path, DEVICE, "CancelPairing")
                with contextlib.suppress(GLib.Error):
                    self.call("/org/bluez", AGENT_MANAGER, "UnregisterAgent", agent)
            if registration:
                self.bus.unregister_object(registration)
            context.pop_thread_default()


class Engine:
    def __init__(self, identity_file):
        self.identity_file = identity_file
        self.bluetooth = Bluetooth()

    def saved_address(self):
        try:
            value = self.identity_file.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise Problem("Não foi possível ler a associação local da caixa.") from exc
        if not ADDRESS.fullmatch(value):
            raise Problem("A associação local da caixa está inválida. Consulte o README para refazê-la.")
        return value

    def remember(self, target):
        if self.saved_address() == target.address:
            return
        directory = self.identity_file.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write((target.address + "\n").encode("ascii"))
                file.close()
                temporary.replace(self.identity_file)
            finally:
                temporary.unlink(missing_ok=True)

    def targets(self, objects, identity):
        targets = []
        for path, interfaces in objects.items():
            info = interfaces.get(DEVICE, {})
            address = info.get("Address", "").upper()
            if ADDRESS.fullmatch(address) and (
                (identity and address == identity) or (not identity and info.get("Name") == NAME)
            ):
                targets.append(Target(path, info))
        return targets

    def locate(self):
        objects = self.bluetooth.objects()
        adapters = [path for path, interfaces in objects.items() if ADAPTER in interfaces]
        if not adapters:
            raise Problem("Nenhum adaptador Bluetooth foi encontrado.")
        powered = [path for path in adapters if objects[path][ADAPTER].get("Powered")]
        if not powered:
            raise Problem("Bluetooth desligado. Ative-o nas configurações do sistema.")
        identity = self.saved_address()
        candidates = self.targets(objects, identity)
        paired = [target for target in candidates if target.info.get("Paired")]
        if paired and not any(target.info["Adapter"] in powered for target in paired):
            raise Problem("O adaptador usado pela AL-1182 está desligado.")
        paired = [target for target in paired if target.info["Adapter"] in powered]
        if len(paired) > 1:
            raise Problem("Há mais de um pareamento compatível. Confira a caixa nas configurações Bluetooth.")
        return (paired[0] if paired else None), powered, identity

    def pair_speaker(self, powered, identity):
        objects = self.bluetooth.discover(powered)
        candidates = [
            target for target in self.targets(objects, identity)
            if target.info["Adapter"] in powered
        ]
        if not candidates:
            raise Problem("AL-1182 fora de alcance ou desligada. Ligue a caixa e ative o modo de pareamento.")
        if len(candidates) > 1:
            raise Problem("A busca encontrou mais de uma AL-1182. Deixe uma só disponível ou pareie pelo sistema.")
        target = candidates[0]
        if target.info.get("Blocked"):
            raise Problem("A AL-1182 está bloqueada nas configurações Bluetooth.")
        if not identity and not (
            A2DP in target.info.get("UUIDs", [])
            or target.info.get("Class", 0) & 0x1FFC == 0x0418
        ):
            raise Problem("A busca não confirmou uma caixa de áudio. Pareie a AL-1182 nas configurações Bluetooth.")
        try:
            if not target.info.get("Paired"):
                self.bluetooth.pair(target)
            if A2DP not in self.bluetooth.property(target, "UUIDs"):
                raise Problem("O dispositivo pareado não anunciou áudio A2DP.")
            self.bluetooth.trust(target)
        except GLib.Error as exc:
            raise Problem("Não foi possível parear a AL-1182. Confira o modo de pareamento e tente novamente.") from exc
        self.remember(target)
        return target

    def snapshot(self):
        target, _, _ = self.locate()
        if not target:
            return Result("Pareamento necessário", "Ligue a caixa em modo de pareamento e use Reparar agora.", "warning")
        if target.info.get("Blocked"):
            raise Problem("A AL-1182 está bloqueada nas configurações Bluetooth.")
        sink = speaker_sink(pulse_list("sinks"), target)
        if sink:
            return self.ready(sink)
        if target.info.get("Connected"):
            return Result("Bluetooth conectado", "A saída de áudio A2DP ainda não apareceu.", "warning")
        return Result("Caixa desconectada", "Confira se a AL-1182 está ligada e ao alcance.", "warning")

    @staticmethod
    def ready(sink):
        detail = (
            "Selecionada como saída padrão." if default_sink() == sink["name"]
            else "A2DP disponível. Outra saída está selecionada no sistema."
        )
        if not playback_enabled(sink):
            detail += " A caixa está silenciada ou com volume zero."
        return Result("Conectada em A2DP", detail, "success", True)

    @staticmethod
    def wait_sink(target, seconds):
        deadline = time.monotonic() + seconds
        while True:
            sink = speaker_sink(pulse_list("sinks"), target)
            if sink or time.monotonic() >= deadline:
                return sink
            time.sleep(0.5)

    @staticmethod
    def select_card_profile(target):
        for card in pulse_list("cards"):
            props = card.get("properties", {})
            if props.get("api.bluez5.address", "").upper() != target.address:
                continue
            if is_a2dp(card.get("active_profile") or ""):
                return
            for profile, details in card.get("profiles", {}).items():
                if is_a2dp(profile) and details.get("available") not in (False, "no"):
                    with contextlib.suppress(subprocess.SubprocessError):
                        run("pactl", "set-card-profile", card["name"], profile)
                    return

    def reconnect(self, target):
        if self.bluetooth.property(target, "Connected"):
            sink = self.wait_sink(target, 2)
            if sink:
                return sink
        self.bluetooth.connect_audio(target)
        sink = self.wait_sink(target, 5)
        if sink:
            return sink
        if self.bluetooth.property(target, "Connected"):
            self.select_card_profile(target)
            sink = self.wait_sink(target, 2)
            if sink:
                return sink
            self.bluetooth.disconnect(target)
        time.sleep(1)
        self.bluetooth.connect_audio(target)
        return self.wait_sink(target, 5)

    def repair(self, manual):
        target, powered, identity = self.locate()
        sinks = pulse_list("sinks")
        sink = speaker_sink(sinks, target) if target else None
        if target is None:
            target = self.pair_speaker(powered, identity)
        elif target.info.get("Blocked"):
            raise Problem("A AL-1182 está bloqueada nas configurações Bluetooth.")
        else:
            self.remember(target)
        reconnected = sink is None
        if reconnected:
            sink = self.reconnect(target)
        if not sink:
            if self.bluetooth.property(target, "Connected"):
                raise Problem("Bluetooth conectado, mas a saída A2DP não apareceu nesta tentativa.")
            raise Problem("Não foi possível conectar à AL-1182. Veja se ela está ligada, ao alcance e livre de outro aparelho.")
        if (manual or reconnected) and default_sink() != sink["name"]:
            run("pactl", "set-default-sink", sink["name"])
            if default_sink() != sink["name"]:
                raise Problem("O A2DP voltou, mas não foi possível definir a caixa como saída padrão.")
        result = self.ready(sink)
        try:
            fixed = fix_chatgpt(sink)
        except (OSError, ValueError, subprocess.SubprocessError):
            return Result(result.title, result.detail + " A verificação do áudio do ChatGPT ficou indisponível.",
                          "warning", True)
        if fixed:
            return Result(result.title, result.detail + " Ganho de áudio do ChatGPT restaurado.", "success", True)
        return result

    def sound_test(self):
        target, _, _ = self.locate()
        sink = speaker_sink(pulse_list("sinks"), target) if target else None
        if not sink:
            raise Problem("A saída A2DP está indisponível. Ligue a caixa e use Reparar agora.")
        if not playback_enabled(sink):
            return Result("Teste de som", "A caixa está silenciada ou com volume zero.", "warning", True)
        run("paplay", "--device=" + sink["name"], "--file-format=wav",
            "--stream-name=Teste da AL-1182", data=test_tone(), timeout=8)
        return Result("Teste concluído", "O som foi enviado para a AL-1182.", "success", True)


def test_tone():
    rate = 24000
    samples = bytearray()
    for frequency in (523.25, 659.25, 783.99):
        for frame in range(rate // 5):
            elapsed = frame / rate
            fade = min(1, elapsed / 0.02, (0.2 - elapsed) / 0.06)
            level = int(5000 * fade * math.sin(2 * math.pi * frequency * elapsed))
            samples.extend(struct.pack("<h", level))
        samples.extend(bytes(rate // 50 * 2))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(samples)
    return output.getvalue()


@contextlib.contextmanager
def operation_lock():
    directory = Path(GLib.get_user_runtime_dir()) / "caixa-al1182-linux"
    directory.mkdir(mode=0o700, exist_ok=True)
    with (directory / "motor.lock").open("a", encoding="ascii") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


def perform(action, identity_file):
    try:
        if action == "status":
            return Engine(identity_file).snapshot()
        with operation_lock() as locked:
            if not locked:
                return Result("Verificação em andamento", "Aguarde alguns segundos e tente novamente.")
            engine = Engine(identity_file)
            if action == "test":
                return engine.sound_test()
            return engine.repair(manual=action == "manual")
    except Problem as exc:
        return Result("AL-1182 indisponível", str(exc), "warning")
    except GLib.Error:
        return Result("Falha no Bluetooth", "O sistema perdeu o acesso à caixa ou ao adaptador.", "error")
    except (OSError, ValueError, subprocess.SubprocessError):
        return Result("Falha no áudio", "Confira se o servidor de áudio e as dependências estão disponíveis.", "error")
