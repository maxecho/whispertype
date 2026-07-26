"""WhisperType — значок в строке меню и вся логика «нажал → сказал → появился текст»."""

import logging
import os
import subprocess
import threading
import time

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

from . import autostart
from . import branding as b
from .config import (
    FIRST_RUN_MARKER,
    HOTKEY_PRESETS,
    LANGUAGES,
    LOG_PATH,
    load_config,
    matching_preset,
    save_config,
)
from .hotkey import HotkeyListener, describe
from .inserter import (
    DENIED,
    GRANTED,
    SETTINGS_URL,
    SETTINGS_URL_INPUT,
    accessibility_ok,
    input_monitoring_ok,
    input_monitoring_state,
    insert_text,
    parent_app_path,
    request_accessibility,
    request_input_monitoring,
)
from .recorder import SAMPLE_RATE, Recorder, probe_microphone
from .transcriber import Transcriber

log = logging.getLogger("app")

IDLE, LOADING, RECORDING, WORKING, ERROR = "idle", "loading", "recording", "working", "error"

WORKING_FRAMES = ("·", "··", "···")
SOUND_START = "/System/Library/Sounds/Tink.aiff"
SOUND_STOP = "/System/Library/Sounds/Pop.aiff"
SOUND_FAIL = "/System/Library/Sounds/Basso.aiff"


def play(path, enabled=True):
    if not enabled:
        return
    try:
        subprocess.Popen(
            ["afplay", "-v", "0.35", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        log.debug("Не смог проиграть звук %s", path, exc_info=True)


def already_running():
    """Уже есть живая копия программы?

    Пригождается, когда launchd поднимает программу при входе, а человек тем
    временем открывает её из Finder. Полагаться на LSMultipleInstancesProhibited
    нельзя: launchd запускает исполняемый файл мимо LaunchServices.
    """
    try:
        from AppKit import NSRunningApplication

        others = NSRunningApplication.runningApplicationsWithBundleIdentifier_(b.BUNDLE_ID)
        mine = os.getpid()
        return any(app.processIdentifier() != mine for app in (others or []))
    except Exception:  # noqa: BLE001
        log.debug("Не смог проверить, запущена ли копия", exc_info=True)
        return False


def open_path(path):
    subprocess.Popen(
        ["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


class WhisperApp(rumps.App):
    def __init__(self):
        super().__init__(
            b.APP_NAME,
            title="",
            icon=str(b.MENUBAR_IDLE),
            template=True,
            quit_button=None,
        )
        self.cfg = load_config()
        self.recorder = Recorder(self.cfg.get("input_device"))
        self.transcriber = Transcriber(self.cfg)

        self.state = LOADING
        self.status_note = b.STATUS_LOADING
        self.last_text = ""
        self._state_lock = threading.Lock()
        self._rec_started = 0.0
        self._error_until = 0.0
        self._frame = 0
        self._menu_dirty = True
        self._started_at = time.monotonic()
        self._welcomed = False
        self._ax_ok = accessibility_ok()
        self._last_ax_check = 0.0
        self._last_look = None
        self._muted = False
        self._muted_warned = False
        self._listen_ok = input_monitoring_ok()
        self._listen_warned = False
        self._session = None
        self._last_feed = 0.0

        self._build_menu()
        self.hotkeys = HotkeyListener(self.cfg, self.on_hotkey, self.on_escape)

    # ------------------------------------------------------------------ меню

    def _build_menu(self):
        self.item_status = rumps.MenuItem(b.STATUS_LOADING)
        self.item_toggle = rumps.MenuItem(b.MENU_START, callback=self.menu_toggle)

        self.item_hotkey = rumps.MenuItem(b.MENU_HOTKEY)
        self.hotkey_items = {}
        for label, preset in HOTKEY_PRESETS:
            entry = rumps.MenuItem(label, callback=self.menu_hotkey)
            self.hotkey_items[label] = (entry, preset)
            self.item_hotkey.add(entry)

        self.item_last = rumps.MenuItem(b.STATUS_NOTHING_YET)

        self.item_language = rumps.MenuItem(b.MENU_LANGUAGE)
        self.language_items = {}
        for label, code in LANGUAGES:
            entry = rumps.MenuItem(label, callback=self.menu_language)
            self.language_items[label] = (entry, code)
            self.item_language.add(entry)

        self.item_paste = rumps.MenuItem(b.MENU_PASTE, callback=self.menu_paste)
        self.item_sounds = rumps.MenuItem(b.MENU_SOUNDS, callback=self.menu_sounds)
        self.item_autostart = rumps.MenuItem(b.MENU_AUTOSTART, callback=self.menu_autostart)

        self.menu = [
            self.item_status,
            self.item_toggle,
            None,
            self.item_last,
            None,
            self.item_hotkey,
            self.item_language,
            self.item_paste,
            self.item_sounds,
            self.item_autostart,
            None,
            rumps.MenuItem(b.MENU_HELP, callback=self.menu_help),
            rumps.MenuItem(b.MENU_TROUBLE, callback=self.menu_trouble),
            rumps.MenuItem(b.MENU_ABOUT, callback=self.menu_about),
            None,
            rumps.MenuItem(b.MENU_QUIT, callback=self.menu_quit),
        ]
        self._sync_checkmarks()

    def _sync_checkmarks(self):
        self.item_paste.state = 1 if self.cfg["insert"]["mode"] == "paste" else 0
        self.item_sounds.state = 1 if self.cfg["sounds"] else 0
        self.item_autostart.state = 1 if autostart.enabled() else 0
        for label, (entry, code) in self.language_items.items():
            entry.state = 1 if self.cfg.get("language") == code else 0
        chosen = matching_preset(self.cfg)
        for label, (entry, _preset) in self.hotkey_items.items():
            entry.state = 1 if label == chosen else 0

    def _alert(self, title, message, ok="Понятно", cancel=None):
        return rumps.alert(
            title, message, ok=ok, cancel=cancel, icon_path=str(b.ICON_PNG)
        )

    # ------------------------------------------------------------ состояние

    def _set_state(self, state, note=""):
        self.state = state
        self.status_note = note
        self._menu_dirty = True

    def _fail(self, note):
        log.warning("Не получилось: %s", note)
        play(SOUND_FAIL, self.cfg["sounds"])
        self._error_until = time.monotonic() + 3.0
        self._set_state(ERROR, note)

    # --------------------------------------------------------------- запись

    def on_hotkey(self):
        with self._state_lock:
            if self.state == RECORDING:
                self._stop_recording()
            elif self.state in (IDLE, ERROR):
                self._start_recording()
            elif self.state == LOADING:
                self._fail(b.STATUS_LOADING)
            else:
                log.info("Нажатие пропущено: ещё разбираю предыдущую запись")

    def on_escape(self):
        with self._state_lock:
            if self.state != RECORDING:
                return
            self.recorder.discard()
            if self._session is not None:
                self._session.cancel()
                self._session = None
            log.info("Запись отменена")
            self._set_state(IDLE, b.STATUS_READY)
            play(SOUND_STOP, self.cfg["sounds"])

    def _start_recording(self):
        try:
            self.recorder.start()
        except Exception:  # noqa: BLE001
            log.exception("Микрофон не открылся")
            self._fail(b.MSG_TOO_QUIET)
            return
        # длинную диктовку расшифровываем кусками прямо во время записи,
        # чтобы после остановки ждать только хвост
        self._session = self.transcriber.start_session() if self.transcriber.ready else None
        self._last_feed = 0.0
        self._rec_started = time.monotonic()
        self._set_state(RECORDING)
        play(SOUND_START, self.cfg["sounds"])

    def _feed_session(self):
        """Раз в секунду отдаём сессии накопленный звук — она сама решит, пора ли резать."""
        if self._session is None:
            return
        now = time.monotonic()
        if now - self._last_feed < 1.0:
            return
        self._last_feed = now
        try:
            self._session.feed(self.recorder.snapshot())
        except Exception:  # noqa: BLE001
            log.exception("Сбой фоновой расшифровки — доделаю всё после остановки")
            self._session = None

    def _stop_recording(self):
        audio = self.recorder.stop()
        session, self._session = self._session, None
        self._set_state(WORKING, b.STATUS_WORKING)
        play(SOUND_STOP, self.cfg["sounds"])
        threading.Thread(target=self._process, args=(audio, session), daemon=True).start()

    def _process(self, audio, session=None):
        try:
            seconds = audio.size / SAMPLE_RATE
            if seconds < float(self.cfg["min_seconds"]):
                if session is not None:
                    session.cancel()
                log.info("Запись короче %.2f с — пропускаю", seconds)
                self._set_state(IDLE, b.STATUS_READY)
                return

            # при запрещённом микрофоне macOS отдаёт ровные нули, а не ошибку;
            # без этой проверки это выглядело бы как «не разобрал ни слова»
            if (float(abs(audio).max()) if audio.size else 0.0) < 1e-4:
                if session is not None:
                    session.cancel()
                self._fail(b.MSG_TOO_QUIET)
                return

            started = time.monotonic()
            if session is not None:
                text = session.finish(audio)
                if session.chunks_started:
                    log.info("Кусков расшифровано во время записи: %d", session.chunks_started)
            else:
                text = self.transcriber.transcribe(audio)
            elapsed = time.monotonic() - started

            if not text:
                self._fail(b.MSG_NOT_RECOGNIZED)
                return

            self.last_text = text
            pasted = insert_text(text, self.cfg)
            note = b.msg_done(seconds, elapsed) if pasted else b.MSG_CLIPBOARD_ONLY
            self._set_state(IDLE, note)
            log.info("Готово, символов: %d", len(text))
        except Exception:  # noqa: BLE001
            log.exception("Сбой при расшифровке")
            self._fail(b.MSG_NOT_RECOGNIZED)

    # --------------------------------------------------------- пункты меню

    def menu_toggle(self, _):
        self.on_hotkey()

    def menu_reinsert(self, _):
        if self.last_text:
            insert_text(self.last_text, self.cfg)

    def menu_hotkey(self, sender):
        entry = self.hotkey_items.get(sender.title)
        if entry is None:
            return
        self.cfg["hotkey"].update(entry[1])
        save_config(self.cfg)
        self._restart_hotkeys()
        self._sync_checkmarks()
        if self.state == IDLE:
            self._set_state(IDLE, b.STATUS_READY)

    def menu_language(self, sender):
        for label, (_entry, code) in self.language_items.items():
            if label == sender.title:
                self.cfg["language"] = code
        save_config(self.cfg)
        self._sync_checkmarks()

    def menu_paste(self, _):
        mode = self.cfg["insert"]["mode"]
        self.cfg["insert"]["mode"] = "clipboard_only" if mode == "paste" else "paste"
        save_config(self.cfg)
        self._sync_checkmarks()

    def menu_sounds(self, _):
        self.cfg["sounds"] = not self.cfg["sounds"]
        save_config(self.cfg)
        self._sync_checkmarks()

    def menu_autostart(self, _):
        autostart.toggle()
        self._sync_checkmarks()

    def menu_help(self, _):
        self._alert(b.MENU_HELP, b.help_text(describe(self.cfg)))

    def menu_trouble(self, _):
        has_access = accessibility_ok()
        try:
            probe_microphone()
            mic_ok = True
        except Exception:  # noqa: BLE001
            mic_ok = False
        message = b.trouble_text(
            describe(self.cfg),
            has_access,
            mic_ok,
            self.transcriber.ready,
            LOG_PATH,
            # без разрешения кнопки журнала в диалоге нет — не обещаем её в тексте
            log_button=has_access,
            stale_access=self.hotkeys.muted and self._listen_ok,
            can_listen=self._listen_ok,
        )
        if not self._listen_ok:
            self._ask_input_monitoring()
            return
        if not has_access:
            if self._alert(b.MENU_TROUBLE, message, ok="Открыть настройки", cancel="Закрыть"):
                request_accessibility()
                open_path(SETTINGS_URL)
        elif not self._alert(b.MENU_TROUBLE, message, ok="Закрыть", cancel="Показать журнал"):
            open_path(LOG_PATH)

    def menu_about(self, _):
        self._alert(b.MENU_ABOUT, b.about_text())

    def menu_quit(self, _):
        self.hotkeys.stop()
        rumps.quit_application()

    # ---------------------------------------------------------- разрешения

    def _ask_accessibility(self):
        request_accessibility()  # добавляет WhisperType в системный список
        if self._alert(
            "Остался один шаг",
            b.no_access_text(parent_app_path()),
            ok="Открыть настройки",
            cancel="Позже",
        ):
            open_path(SETTINGS_URL)

    def _ask_input_monitoring(self):
        # системное окно покажется только если разрешение ещё не спрашивали;
        # если его уже запретили, остаётся отправить человека в настройки
        request_input_monitoring()
        if self._alert(
            b.STATUS_NO_LISTEN,
            b.no_listen_text(parent_app_path()),
            ok="Открыть настройки",
            cancel="Позже",
        ):
            open_path(SETTINGS_URL_INPUT)

    def _poll_permissions(self):
        now = time.monotonic()
        if now - self._last_ax_check < 2.0:
            return
        self._last_ax_check = now

        listen_ok = input_monitoring_ok()
        if listen_ok != self._listen_ok:
            self._listen_ok = listen_ok
            self._menu_dirty = True
            if listen_ok:
                # разрешение только что выдали — старый перехват уже мёртв
                log.info("Мониторинг ввода разрешён, поднимаю перехват заново")
                self._restart_hotkeys()
                if self.state == IDLE:
                    self._set_state(IDLE, b.STATUS_READY)

        muted = self.hotkeys.muted
        if muted != self._muted:
            self._muted = muted
            self._menu_dirty = True
        if muted and not self._listen_ok:
            muted = False  # причина известна и объясняется отдельным окном
        if muted and not self._muted_warned:
            self._muted_warned = True
            log.warning("Разрешение устарело: система глушит перехват нажатий")
            if self._alert(
                b.STATUS_STALE_ACCESS,
                b.stale_access_text(),
                ok="Открыть настройки",
                cancel="Позже",
            ):
                open_path(SETTINGS_URL)
        granted = accessibility_ok()
        if granted == self._ax_ok:
            return
        self._ax_ok = granted
        self._menu_dirty = True
        if granted:
            # слушатель, созданный до выдачи прав, событий уже не увидит —
            # поднимаем новый, чтобы не заставлять перезапускать программу
            log.info("Разрешение выдано, поднимаю слушатель заново")
            self._restart_hotkeys()
            if self.state == IDLE:
                self._set_state(IDLE, b.STATUS_READY)
        else:
            log.warning("Разрешение отозвано")

    def _restart_hotkeys(self):
        try:
            self.hotkeys.stop()
        except Exception:  # noqa: BLE001
            log.debug("Старый слушатель не остановился", exc_info=True)
        self.hotkeys = HotkeyListener(self.cfg, self.on_hotkey, self.on_escape)
        self.hotkeys.start()
        self._muted = False
        self._muted_warned = False

    # ------------------------------------------------------------------ вид

    def _look(self):
        """Как сейчас должен выглядеть значок: (иконка, шаблонная ли, подпись)."""
        if self.state == RECORDING:
            elapsed = int(time.monotonic() - self._rec_started)
            return b.MENUBAR_RECORDING, False, f" {elapsed // 60}:{elapsed % 60:02d}"
        if self.state in (WORKING, LOADING):
            self._frame = (self._frame + 1) % len(WORKING_FRAMES)
            return b.MENUBAR_IDLE, True, f" {WORKING_FRAMES[self._frame]}"
        if self.state == ERROR:
            return b.MENUBAR_IDLE, True, " !"
        healthy = self._ax_ok and self._listen_ok and not self._muted
        return b.MENUBAR_IDLE, True, "" if healthy else " !"

    def tick(self, _):
        self._poll_permissions()

        look = self._look()
        if look != self._last_look:  # лишний раз строку меню не трогаем
            icon, template, title = look
            self._last_look = look
            self.template = template
            self.icon = str(icon)
            self.title = title

        # окна показываем только с главного потока, то есть отсюда
        if not self._welcomed and time.monotonic() - self._started_at > 1.0:
            self._welcomed = True
            try:
                self._welcome()
            except Exception:  # noqa: BLE001
                log.debug("Приветствие не показалось", exc_info=True)

        if self.state == RECORDING:
            self._feed_session()
            if time.monotonic() - self._rec_started >= float(self.cfg["max_seconds"]):
                log.info("Дошли до предела длины записи — останавливаю сам")
                with self._state_lock:
                    if self.state == RECORDING:
                        self._stop_recording()
        elif self.state == ERROR and time.monotonic() > self._error_until:
            self._set_state(IDLE, b.STATUS_READY)

        if not self._menu_dirty:
            return
        self._menu_dirty = False
        self._refresh_menu()

    def _refresh_menu(self):
        self.item_toggle.title = b.MENU_STOP if self.state == RECORDING else b.MENU_START
        if not self._listen_ok or not self._ax_ok or self._muted:
            if not self._listen_ok:
                self.item_status.title = b.STATUS_NO_LISTEN
            elif not self._ax_ok:
                self.item_status.title = b.STATUS_NO_ACCESS
            else:
                self.item_status.title = b.STATUS_STALE_ACCESS
            self.item_status.set_callback(self.menu_trouble)
        else:
            self.item_status.set_callback(None)
            self.item_status.title = self.status_note or {
                IDLE: b.STATUS_READY,
                LOADING: b.STATUS_LOADING,
                RECORDING: b.STATUS_RECORDING,
                WORKING: b.STATUS_WORKING,
            }.get(self.state, b.STATUS_READY)

        if self.last_text:
            preview = self.last_text
            if len(preview) > 55:
                preview = preview[:52].rstrip() + "…"
            self.item_last.title = f"Вставить ещё раз: «{preview}»"
            self.item_last.set_callback(self.menu_reinsert)
        else:
            self.item_last.title = b.STATUS_NOTHING_YET
            self.item_last.set_callback(None)

    # ---------------------------------------------------------------- старт

    def _warmup(self):
        # дёргаем микрофон заранее: запрос доступа всплывёт сейчас, а не посреди
        # первой диктовки (пока доступа нет, macOS молча отдаёт тишину)
        try:
            probe_microphone()
        except Exception:  # noqa: BLE001
            log.warning("Микрофон при старте недоступен", exc_info=True)

        if self.transcriber.warmup():
            self._set_state(IDLE, b.STATUS_READY)
        else:
            self._fail(b.MSG_MODEL_BROKEN)

    def _welcome(self):
        if not FIRST_RUN_MARKER.exists():
            FIRST_RUN_MARKER.parent.mkdir(parents=True, exist_ok=True)
            FIRST_RUN_MARKER.write_text("ok\n", "utf-8")
            self._alert(
                f"{b.APP_NAME} на месте",
                b.welcome_text(describe(self.cfg)),
                ok="Понятно",
            )
        if not self._ax_ok:
            self._ask_accessibility()
        if not self._listen_ok and not self._listen_warned:
            self._listen_warned = True
            self._ask_input_monitoring()

    def run(self):
        # живём только в строке меню, без значка в Dock
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
        listen = input_monitoring_state()
        log.info(
            "%s %s запускается. Нажимать клавиши: %s. Слышать нажатия: %s",
            b.APP_NAME, b.VERSION,
            "есть" if self._ax_ok else "нет",
            {GRANTED: "есть", DENIED: "ЗАПРЕЩЕНО"}.get(listen, "ещё не спрашивали"),
        )
        threading.Thread(target=self._warmup, daemon=True).start()
        self.hotkeys.start()
        self._started_at = time.monotonic()
        rumps.Timer(self.tick, 0.2).start()
        super().run()


def main():
    if already_running():
        log.info("Копия уже работает — вторую не запускаю")
        return
    WhisperApp().run()
