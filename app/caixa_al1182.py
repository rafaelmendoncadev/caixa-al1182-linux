#!/usr/bin/python3
"""Aplicativo GTK e entrada do serviço da AL-1182."""

import argparse
from pathlib import Path
import threading
import time

from gi.repository import GLib

from engine import perform

APP_ID = "io.github.rafaelmendoncadev.CaixaAL1182"


def show_window(identity_file, icon_file):
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, Gtk

    Gdk.set_program_class(APP_ID)

    class SpeakerWindow(Gtk.ApplicationWindow):
        def __init__(self, application):
            super().__init__(application=application, title="Caixa AL-1182")
            self.set_default_size(410, 210)
            try:
                icon = GdkPixbuf.Pixbuf.new_from_file(str(icon_file))
                self.set_icon(icon)
                speaker_image = Gtk.Image.new_from_pixbuf(
                    icon.scale_simple(64, 64, GdkPixbuf.InterpType.BILINEAR)
                )
            except GLib.Error:
                self.set_icon_name("audio-speakers")
                speaker_image = Gtk.Image.new_from_icon_name("audio-speakers", Gtk.IconSize.DIALOG)
            self.busy = False
            self.refresh_after = 0
            self.refresh_source = 0

            layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin=22)
            self.add(layout)

            status = Gtk.Box(spacing=14)
            status.set_hexpand(True)
            layout.pack_start(status, True, True, 0)
            speaker_image.set_valign(Gtk.Align.START)
            status.pack_start(speaker_image, False, False, 0)

            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            status.pack_start(labels, True, True, 0)
            self.title = Gtk.Label(label="Verificando a AL-1182…", xalign=0)
            self.title.get_style_context().add_class("title")
            self.title.set_line_wrap(True)
            labels.pack_start(self.title, False, False, 0)
            self.detail = Gtk.Label(xalign=0)
            self.detail.set_line_wrap(True)
            self.detail.set_max_width_chars(42)
            labels.pack_start(self.detail, False, False, 0)

            actions = Gtk.ButtonBox()
            actions.set_spacing(8)
            actions.set_layout(Gtk.ButtonBoxStyle.END)
            layout.pack_end(actions, False, False, 0)

            self.test = Gtk.Button.new_with_mnemonic("_Testar som")
            self.test.connect("clicked", lambda _button: self.start("test"))
            self.test.set_sensitive(False)
            actions.add(self.test)
            self.repair = Gtk.Button.new_with_mnemonic("_Reparar agora")
            self.repair.get_style_context().add_class("suggested-action")
            self.repair.connect("clicked", lambda _button: self.start("manual"))
            actions.add(self.repair)

            self.show_all()
            self.refresh_source = GLib.timeout_add_seconds(5, self.refresh)
            self.connect("destroy", self.stop_refresh)
            self.start("status")

        def refresh(self):
            if time.monotonic() >= self.refresh_after:
                self.start("status")
            return GLib.SOURCE_CONTINUE

        def stop_refresh(self, _window):
            if self.refresh_source:
                GLib.source_remove(self.refresh_source)
                self.refresh_source = 0

        def start(self, action):
            if self.busy:
                return
            self.busy = True
            application = self.get_application()
            if action != "status":
                application.hold()
                self.repair.set_sensitive(False)
                self.test.set_sensitive(False)
                self.title.set_text("Reparando a conexão…" if action == "manual" else "Tocando o teste…")
                self.detail.set_text("A busca pela caixa pode levar alguns segundos." if action == "manual" else "")

            def work():
                result = perform(action, identity_file)
                GLib.idle_add(self.show_result, result, action, application)

            threading.Thread(target=work, daemon=True).start()

        def show_result(self, result, action, application):
            if action != "status":
                application.release()
            if not self.refresh_source:
                return GLib.SOURCE_REMOVE
            self.busy = False
            if action != "status":
                self.refresh_after = time.monotonic() + 8
            self.title.set_text(result.title)
            self.detail.set_text(result.detail)
            self.repair.set_sensitive(True)
            self.test.set_sensitive(result.has_sink)
            return GLib.SOURCE_REMOVE

    class Application(Gtk.Application):
        def do_activate(self):
            window = self.get_active_window() or SpeakerWindow(self)
            window.present()

    GLib.set_prgname(APP_ID)
    GLib.set_application_name("Caixa AL-1182")
    application = Application(application_id=APP_ID)
    return application.run([])


def main():
    parser = argparse.ArgumentParser(description="Configuração de áudio Bluetooth da AL-1182")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--status", dest="action", action="store_const", const="status")
    commands.add_argument("--automatico", dest="action", action="store_const", const="automatic")
    commands.add_argument("--reparar", dest="action", action="store_const", const="manual")
    commands.add_argument("--testar", dest="action", action="store_const", const="test")
    parser.add_argument("--identidade", type=Path, default=(
        Path(GLib.get_user_state_dir()) / "caixa-al1182-linux" / "dispositivo"
    ), help=argparse.SUPPRESS)
    parser.add_argument("--icone", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.action:
        source_icon = Path(__file__).resolve().parents[1] / "packaging" / "caixa-al1182.png"
        installed_icon = Path(GLib.get_user_data_dir()) / "icons/hicolor/256x256/apps" / (APP_ID + ".png")
        icon_file = args.icone or (source_icon if source_icon.is_file() else installed_icon)
        return show_window(args.identidade, icon_file)
    result = perform(args.action, args.identidade)
    print(f"{result.title}. {result.detail}", flush=True)
    # Indisponibilidade temporária da caixa é esperada na rotina automática.
    if result.kind == "error" or (result.kind == "warning" and args.action != "automatic"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
