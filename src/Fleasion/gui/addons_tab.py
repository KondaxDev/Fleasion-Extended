"""Addons tab — Account Health, Creator Watch, Community Plugins."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..utils.logging import log_buffer

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def _on_main_thread(fn):
    """Schedule *fn* to run on the Qt main thread via a zero-delay singleShot."""
    QTimer.singleShot(0, fn)


class AddonsTab(QWidget):
    """Tab grouping Account Health, Creator Watch and Community Plugins."""

    def __init__(self, rando_tab=None, proxy_master=None, parent=None):
        super().__init__(parent)
        self._rando_tab = rando_tab
        self._proxy_master = proxy_master
        self._setup_ui()

        # Wire up to rando_tab health-update signal if available
        if rando_tab is not None and hasattr(rando_tab, "account_health_updated"):
            rando_tab.account_health_updated.connect(self._refresh_health_list)

    # ------------------------------------------------------------------ #
    # UI setup                                                             #
    # ------------------------------------------------------------------ #

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ── Account Health ────────────────────────────────────────────────
        ah_group = QGroupBox("Account Health")
        ahl = QVBoxLayout(ah_group)

        ahl.addWidget(QLabel(
            "Check the health of your saved accounts. "
            "A green dot means the cookie is valid; orange means expired; "
            "red means banned."
        ))

        self._health_list = QListWidget()
        self._health_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._health_list.customContextMenuRequested.connect(self._on_health_ctx_menu)
        ahl.addWidget(self._health_list)

        ah_btn_row = QHBoxLayout()
        self._check_all_btn = QPushButton("Check Health (All Accounts)")
        self._check_all_btn.clicked.connect(self._on_check_all)
        ah_btn_row.addWidget(self._check_all_btn)
        self._refresh_health_btn = QPushButton("Refresh List")
        self._refresh_health_btn.clicked.connect(self._refresh_health_list)
        ah_btn_row.addWidget(self._refresh_health_btn)
        ah_btn_row.addStretch()
        ahl.addLayout(ah_btn_row)

        root.addWidget(ah_group)
        self._refresh_health_list()

        # ── Creator Watch ─────────────────────────────────────────────────
        cw_group = QGroupBox("Creator Watch (Discord notifications)")
        cwl = QVBoxLayout(cw_group)
        cwl.addWidget(QLabel(
            "Receive a Discord webhook notification when a watched creator (account or group) "
            "publishes a new asset intercepted by the cache scraper."
        ))
        self._creator_watch_list = QListWidget()
        self._creator_watch_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._creator_watch_list.customContextMenuRequested.connect(self._on_creator_watch_ctx_menu)
        cwl.addWidget(self._creator_watch_list)
        cw_btn_row = QHBoxLayout()
        self._cw_add_btn = QPushButton("Add Watch…")
        self._cw_add_btn.clicked.connect(self._on_add_creator_watch)
        cw_btn_row.addWidget(self._cw_add_btn)
        cw_btn_row.addStretch()
        cwl.addLayout(cw_btn_row)
        root.addWidget(cw_group)
        self._populate_creator_watch_list()

        # ── Community Plugins ─────────────────────────────────────────────
        plugins_group = QGroupBox("Community Plugins")
        pl = QVBoxLayout(plugins_group)
        pl.addWidget(QLabel(
            "Python addons written by the community. Drop your .py files into the "
            "community/ folder of the repo — they will be loaded automatically on the "
            "next proxy start."
        ))
        self._plugins_list = QListWidget()
        self._plugins_list.setMaximumHeight(130)
        self._plugins_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._plugins_list.customContextMenuRequested.connect(self._on_plugins_ctx_menu)
        pl.addWidget(self._plugins_list)
        pl_btn_row = QHBoxLayout()
        self._plugins_refresh_btn = QPushButton("Refresh")
        self._plugins_refresh_btn.setToolTip(
            "Rescan the community/ folder for new plugin files."
        )
        self._plugins_refresh_btn.clicked.connect(self._on_plugins_refresh)
        pl_btn_row.addWidget(self._plugins_refresh_btn)
        self._plugins_open_dir_btn = QPushButton("Open Folder…")
        self._plugins_open_dir_btn.setToolTip("Open the community/ folder in the file explorer.")
        self._plugins_open_dir_btn.clicked.connect(self._on_plugins_open_dir)
        pl_btn_row.addWidget(self._plugins_open_dir_btn)
        pl_btn_row.addStretch()
        pl.addLayout(pl_btn_row)
        root.addWidget(plugins_group)
        self._populate_plugins_list()

        root.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------ #
    # Account Health                                                        #
    # ------------------------------------------------------------------ #

    def _refresh_health_list(self):
        """Rebuild the health list from the rando_tab's account data."""
        self._health_list.clear()
        rt = self._rando_tab
        accounts = getattr(rt, "_accounts", []) if rt is not None else []

        if not accounts:
            placeholder = QListWidgetItem("No accounts added yet — add accounts in the Miscellaneous tab.")
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._health_list.addItem(placeholder)
            return

        from ..gui.rando_stuff_tab import HEALTH_DOTS, HEALTH_LABELS
        for idx, acc in enumerate(accounts):
            username = acc.get("username", "(unknown)")
            status = acc.get("health_status", "unknown")
            label = acc.get("health_label", HEALTH_LABELS.get(status, "Not checked"))
            dot = HEALTH_DOTS.get(status, "⚪")
            checked_at = acc.get("health_checked_at")
            if checked_at:
                import datetime
                ts = datetime.datetime.fromtimestamp(checked_at).strftime("%H:%M:%S")
                text = f"{dot} {username}  —  {label}  (checked {ts})"
            else:
                text = f"{dot} {username}  —  {label}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self._health_list.addItem(item)

    def _on_check_all(self):
        rt = self._rando_tab
        if rt is None:
            return
        if not getattr(rt, "_accounts", []):
            QMessageBox.information(self, "No Accounts", "No accounts to check yet.")
            return
        rt._check_all_accounts_health()

    def _on_health_ctx_menu(self, pos):
        item = self._health_list.itemAt(pos)
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return
        menu = QMenu(self)
        check_action = menu.addAction("Check Health")
        action = menu.exec(self._health_list.viewport().mapToGlobal(pos))
        if action == check_action and self._rando_tab is not None:
            self._rando_tab._check_account_health_async(idx)

    # ------------------------------------------------------------------ #
    # Creator Watch                                                         #
    # ------------------------------------------------------------------ #

    def _get_creator_watcher(self):
        scraper = getattr(self._proxy_master, "cache_scraper", None)
        return getattr(scraper, "creator_watcher", None) if scraper is not None else None

    def _populate_creator_watch_list(self):
        self._creator_watch_list.clear()
        watcher = self._get_creator_watcher()
        if watcher is None:
            return
        for w in watcher.list_watches():
            kind = "Group" if w.get("creator_type") == 2 else "User"
            item = QListWidgetItem(f"{w.get('label')} ({kind}, ID {w.get('creator_id')})")
            item.setToolTip(w.get("webhook_url", ""))
            self._creator_watch_list.addItem(item)

    def _on_add_creator_watch(self):
        watcher = self._get_creator_watcher()
        if watcher is None:
            QMessageBox.warning(
                self,
                "Proxy Not Started",
                "Start the Fleasion proxy at least once before adding a Creator Watch.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Creator Watch")
        dialog.setMinimumWidth(440)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel("Username, group name, or numeric ID:"))
        creator_input = QLineEdit()
        creator_input.setPlaceholderText("e.g. Builderman, or a group name, or 156")
        layout.addWidget(creator_input)

        resolved_lbl = QLabel("")
        layout.addWidget(resolved_lbl)

        layout.addWidget(QLabel("Discord webhook URL:"))
        webhook_input = QLineEdit()
        webhook_input.setPlaceholderText("https://discord.com/api/webhooks/...")
        layout.addWidget(webhook_input)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Add")
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        resolved: dict = {"id": None, "type": None, "label": None}

        def _resolve():
            raw = creator_input.text().strip()
            if not raw:
                return
            ok_btn.setEnabled(False)
            resolved_lbl.setText("Resolving…")

            def _run():
                from ..proxy.addons.creator_watch import resolve_creator_for_input
                cid, ctype, label = resolve_creator_for_input(raw)

                def _done():
                    ok_btn.setEnabled(True)
                    if cid is None:
                        resolved_lbl.setText("Not found. Check the name/ID.")
                        return
                    resolved["id"] = cid
                    resolved["type"] = ctype
                    resolved["label"] = label or raw
                    kind = "Group" if ctype == 2 else "User"
                    resolved_lbl.setText(f"Found: {label} ({kind}, ID {cid})")

                _on_main_thread(_done)

            threading.Thread(target=_run, daemon=True).start()

        creator_input.editingFinished.connect(_resolve)

        def _on_ok():
            webhook_url = webhook_input.text().strip()
            if not webhook_url.startswith("https://discord.com/api/webhooks/") and \
               not webhook_url.startswith("https://discordapp.com/api/webhooks/"):
                resolved_lbl.setText("Invalid webhook — paste a Discord webhook URL.")
                return
            if resolved["id"] is None:
                resolved_lbl.setText("Enter the name/ID and leave the field to resolve it first.")
                return
            watcher.add_watch(resolved["id"], resolved["type"], resolved["label"], webhook_url)
            dialog.accept()

        ok_btn.clicked.connect(_on_ok)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._populate_creator_watch_list()
            log_buffer.log("CreatorWatch", f"Added watch for {resolved['label']} (ID {resolved['id']})")

    def _on_creator_watch_ctx_menu(self, pos):
        item = self._creator_watch_list.itemAt(pos)
        if item is None:
            return
        idx = self._creator_watch_list.row(item)
        watcher = self._get_creator_watcher()
        if watcher is None:
            return
        menu = QMenu(self)
        remove_action = menu.addAction("Remove")
        action = menu.exec(self._creator_watch_list.viewport().mapToGlobal(pos))
        if action == remove_action:
            watcher.remove_watch(idx)
            self._populate_creator_watch_list()

    # ------------------------------------------------------------------ #
    # Community Plugins                                                     #
    # ------------------------------------------------------------------ #

    def _get_plugin_loader(self):
        pm = self._proxy_master
        if pm is None:
            return None
        return getattr(pm, "plugin_loader", None)

    def _populate_plugins_list(self):
        self._plugins_list.clear()
        loader = self._get_plugin_loader()
        if loader is None:
            item = QListWidgetItem("(proxy not started — list will be available after starting)")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._plugins_list.addItem(item)
            return
        plugins = loader.list_plugins()
        if not plugins:
            item = QListWidgetItem("No addons found in community/ (the example doesn't count).")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._plugins_list.addItem(item)
            return
        for p in plugins:
            if p.get("error"):
                label = f"⚠ {p['name']}  —  ERROR: {p['error']}"
            elif p.get("enabled"):
                label = f"✅ {p['name']} v{p['version']}  —  {p['description']}"
            else:
                label = f"⬜ {p['name']} v{p['version']}  —  {p['description']} (disabled)"
            item = QListWidgetItem(label)
            item.setToolTip(
                f"Module: {p['module']}.py\n"
                f"Author: {p['author']}\n"
                f"Version: {p['version']}\n"
                + (f"Error: {p['error']}" if p.get("error") else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, p["module"])
            self._plugins_list.addItem(item)

    def _on_plugins_refresh(self):
        loader = self._get_plugin_loader()
        if loader is not None:
            loader.reload()
        self._populate_plugins_list()

    def _on_plugins_open_dir(self):
        import subprocess
        from ..proxy.addons.community import __file__ as _community_init
        community_dir = Path(_community_init).parent
        community_dir.mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS:
            subprocess.Popen(["explorer", str(community_dir)])
        elif IS_MACOS:
            subprocess.Popen(["open", str(community_dir)])
        else:
            subprocess.Popen(["xdg-open", str(community_dir)])

    def _on_plugins_ctx_menu(self, pos):
        item = self._plugins_list.itemAt(pos)
        if item is None:
            return
        module_name = item.data(Qt.ItemDataRole.UserRole)
        if not module_name:
            return
        loader = self._get_plugin_loader()
        if loader is None:
            return
        plugins = {p["module"]: p for p in loader.list_plugins()}
        p = plugins.get(module_name)
        if p is None:
            return
        menu = QMenu(self)
        if p.get("error"):
            menu.addAction("(Addon in error — fix the .py file)").setEnabled(False)
        else:
            toggle_label = "Disable" if p.get("enabled") else "Enable"
            toggle_action = menu.addAction(toggle_label)
            action = menu.exec(self._plugins_list.viewport().mapToGlobal(pos))
            if action == toggle_action:
                loader.set_plugin_enabled(module_name, not p.get("enabled", True))
                self._populate_plugins_list()
            return
        menu.exec(self._plugins_list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ #
    # Public refresh                                                        #
    # ------------------------------------------------------------------ #

    def refresh_all(self):
        """Called externally when account health changes or proxy connects."""
        self._refresh_health_list()
        self._populate_creator_watch_list()
        self._populate_plugins_list()
