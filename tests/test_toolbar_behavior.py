from __future__ import annotations

import importlib
import sys
import types


class Widget:
    def __init__(self) -> None:
        self.parent = None

    def get_parent(self):
        return self.parent

    def get_first_child(self):
        return None

    def get_next_sibling(self):
        return None


class Image(Widget):
    def __init__(self, icon_name: str | None = None) -> None:
        super().__init__()
        self.icon_name = icon_name
        self.css_classes: set[str] = set()

    @classmethod
    def new_from_icon_name(cls, icon_name: str):
        return cls(icon_name)

    def set_from_icon_name(self, icon_name: str) -> None:
        self.icon_name = icon_name

    def add_css_class(self, name: str) -> None:
        self.css_classes.add(name)

    def remove_css_class(self, name: str) -> None:
        self.css_classes.discard(name)


class Button(Widget):
    def __init__(self) -> None:
        super().__init__()
        self.child = None
        self.tooltip = None
        self.visible = True
        self.sensitive = True
        self.css_classes: set[str] = set()
        self.connections: list[tuple[str, object]] = []
        self.previous = None

    def set_child(self, child) -> None:
        self.child = child
        child.parent = self

    def get_child(self):
        return self.child

    def set_tooltip_text(self, text: str) -> None:
        self.tooltip = text

    def get_tooltip_text(self):
        return self.tooltip

    def add_css_class(self, name: str) -> None:
        self.css_classes.add(name)

    def remove_css_class(self, name: str) -> None:
        self.css_classes.discard(name)

    def set_visible(self, value: bool) -> None:
        self.visible = value

    def set_sensitive(self, value: bool) -> None:
        self.sensitive = value

    def connect(self, signal: str, callback) -> None:
        self.connections.append((signal, callback))

    def get_prev_sibling(self):
        return self.previous


class Box(Widget):
    def __init__(self) -> None:
        super().__init__()
        self.insertions: list[tuple[Widget, Widget | None]] = []
        self.prepended: list[Widget] = []

    def insert_child_after(self, child: Widget, sibling: Widget | None) -> None:
        child.parent = self
        self.insertions.append((child, sibling))

    def prepend(self, child: Widget) -> None:
        child.parent = self
        self.prepended.append(child)

    def remove(self, child: Widget) -> None:
        if child.parent is self:
            child.parent = None


class CssProvider:
    last = None

    def __init__(self) -> None:
        self.data = b""
        CssProvider.last = self

    def load_from_data(self, data: bytes) -> None:
        self.data = data


class StyleContext:
    providers: list[tuple[object, object, int]] = []

    @classmethod
    def add_provider_for_display(cls, display, provider, priority: int) -> None:
        cls.providers.append((display, provider, priority))


class Display:
    instance = object()

    @classmethod
    def get_default(cls):
        return cls.instance


class BareContact:
    pass


class MessageActionsBox(Widget):
    def __init__(self, contact=None) -> None:
        super().__init__()
        self.contact = contact

    def get_current_contact(self):
        return self.contact


class GajimPlugin:
    pass


def load_plugin_module(monkeypatch):
    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.Gdk = types.SimpleNamespace(Display=Display)
    repository.GLib = types.SimpleNamespace(
        SOURCE_REMOVE=False,
        source_remove=lambda _source: None,
        timeout_add=lambda *_args: 1,
        idle_add=lambda *_args: 1,
    )
    repository.Gtk = types.SimpleNamespace(
        Widget=Widget,
        Button=Button,
        Image=Image,
        CssProvider=CssProvider,
        StyleContext=StyleContext,
        STYLE_PROVIDER_PRIORITY_APPLICATION=600,
    )
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    gajim = types.ModuleType("gajim")
    common = types.ModuleType("gajim.common")
    common.app = types.SimpleNamespace(window=None)
    common_modules = types.ModuleType("gajim.common.modules")
    contacts = types.ModuleType("gajim.common.modules.contacts")
    contacts.BareContact = BareContact
    gtk_pkg = types.ModuleType("gajim.gtk")
    alert = types.ModuleType("gajim.gtk.alert")
    alert.InformationAlertDialog = type("InformationAlertDialog", (), {})
    message_actions = types.ModuleType("gajim.gtk.message_actions_box")
    message_actions.MessageActionsBox = MessageActionsBox
    plugins = types.ModuleType("gajim.plugins")
    plugins.GajimPlugin = GajimPlugin

    monkeypatch.setitem(sys.modules, "gajim", gajim)
    monkeypatch.setitem(sys.modules, "gajim.common", common)
    monkeypatch.setitem(sys.modules, "gajim.common.modules", common_modules)
    monkeypatch.setitem(sys.modules, "gajim.common.modules.contacts", contacts)
    monkeypatch.setitem(sys.modules, "gajim.gtk", gtk_pkg)
    monkeypatch.setitem(sys.modules, "gajim.gtk.alert", alert)
    monkeypatch.setitem(sys.modules, "gajim.gtk.message_actions_box", message_actions)
    monkeypatch.setitem(sys.modules, "gajim.plugins", plugins)

    fake_controller = types.ModuleType("gajim_calls.controller")
    fake_controller.WebRTCMediaEngine = object
    fake_module = types.ModuleType("gajim_calls.module")
    fake_module.set_controller = lambda _controller: None
    fake_alerts = types.ModuleType("gajim_calls.alerts")
    fake_alerts.IncomingCallAlerts = type("IncomingCallAlerts", (), {})
    fake_runtime = types.ModuleType("gajim_calls.controller_runtime")
    fake_runtime.RuntimeCallController = type("RuntimeCallController", (), {})
    fake_config = types.ModuleType("gajim_calls.gtk.config")
    fake_config.ConfigDialog = type("ConfigDialog", (), {})
    fake_media = types.ModuleType("gajim_calls.media_engine")
    fake_media.WebRTCMediaEngine = object
    fake_media.probe_runtime = lambda: (True, "")
    fake_media.probe_video_runtime = lambda: (True, "")
    fake_caps = types.ModuleType("gajim_calls.capabilities")
    fake_caps.extend_call_capabilities = lambda features, *, video: features

    monkeypatch.setitem(sys.modules, "gajim_calls.controller", fake_controller)
    monkeypatch.setitem(sys.modules, "gajim_calls.module", fake_module)
    monkeypatch.setitem(sys.modules, "gajim_calls.alerts", fake_alerts)
    monkeypatch.setitem(sys.modules, "gajim_calls.controller_runtime", fake_runtime)
    monkeypatch.setitem(sys.modules, "gajim_calls.gtk.config", fake_config)
    monkeypatch.setitem(sys.modules, "gajim_calls.media_engine", fake_media)
    monkeypatch.setitem(sys.modules, "gajim_calls.capabilities", fake_caps)

    sys.modules.pop("gajim_calls.plugin", None)
    return importlib.import_module("gajim_calls.plugin")


def fresh_plugin(plugin_module):
    plugin = object.__new__(plugin_module.GajimCallsPlugin)
    plugin._toolbar_entry = None
    plugin._toolbar_retry_id = None
    plugin._toolbar_retry_attempts = 0
    plugin._startup_retry_id = None
    plugin._startup_retry_attempts = 0
    plugin._call_active = False
    plugin._call_connected = False
    plugin._css_provider = None
    return plugin


def test_cold_start_discovers_existing_chat_and_creates_call_button(monkeypatch):
    module = load_plugin_module(monkeypatch)
    plugin = fresh_plugin(module)
    box = MessageActionsBox(BareContact())
    plugin._find_current_message_actions_box = lambda: box
    plugin._schedule_toolbar_attach = lambda: None

    assert plugin._ensure_toolbar_registered() is False

    assert plugin._toolbar_entry is not None
    assert plugin._toolbar_entry[0] is box
    button = plugin._toolbar_entry[2]
    assert button.visible
    assert button.tooltip == "Start audio call"
    image = button.get_child()
    assert isinstance(image, Image)
    assert image.icon_name == "call-start-symbolic"
    assert "gajim-calls-mirrored-phone" in image.css_classes


def test_call_button_is_inserted_immediately_left_of_search(monkeypatch):
    module = load_plugin_module(monkeypatch)
    plugin = fresh_plugin(module)
    message_box = MessageActionsBox(BareContact())
    old_parent = Box()
    button = Button()
    button.set_child(Image.new_from_icon_name("call-start-symbolic"))
    button.parent = old_parent
    plugin._toolbar_entry = (message_box, old_parent, button)

    toolbar = Box()
    previous = Button()
    search = Button()
    search.previous = previous
    plugin._find_chat_toolbar_target = lambda: (toolbar, search)

    assert plugin._attach_toolbar_control() is False
    assert toolbar.insertions == [(button, previous)]
    assert plugin._toolbar_entry == (message_box, toolbar, button)


def test_toolbar_icon_and_tooltip_follow_call_state(monkeypatch):
    module = load_plugin_module(monkeypatch)
    plugin = fresh_plugin(module)
    button = Button()
    image = Image.new_from_icon_name("call-start-symbolic")
    image.add_css_class("gajim-calls-mirrored-phone")
    button.set_child(image)
    plugin._toolbar_entry = (MessageActionsBox(BareContact()), Box(), button)

    plugin.set_call_active(True, connected=False)
    assert image.icon_name == "call-stop-symbolic"
    assert "gajim-calls-mirrored-phone" not in image.css_classes
    assert button.tooltip == "Cancel call"
    assert "destructive-action" in button.css_classes

    plugin.set_call_active(True, connected=True)
    assert button.tooltip == "Hang up"

    plugin.set_call_active(False)
    assert image.icon_name == "call-start-symbolic"
    assert "gajim-calls-mirrored-phone" in image.css_classes
    assert button.tooltip == "Start audio call"
    assert "destructive-action" not in button.css_classes


def test_mirrored_phone_css_is_installed_at_runtime(monkeypatch):
    module = load_plugin_module(monkeypatch)
    plugin = fresh_plugin(module)

    plugin._install_css()

    provider = CssProvider.last
    assert provider is not None
    assert b".gajim-calls-mirrored-phone" in provider.data
    assert b"scaleX(-1)" in provider.data
    assert plugin._css_provider is provider
