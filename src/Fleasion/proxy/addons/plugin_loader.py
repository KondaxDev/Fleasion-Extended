"""PluginLoader — Chargement dynamique des addons communautaires Fleasion.

Scanne ``proxy/addons/community/`` au démarrage, importe chaque module .py,
instancie la première sous-classe de ``CommunityAddon`` trouvée, et enregistre
l'instance auprès du CacheManager (hook on_asset_cached) et du ProxyMaster
(hooks request/response via _module_interceptors).

API publique utilisée par la GUI (RandoStuffTab)
-------------------------------------------------
  loader.list_plugins()           → list[dict]   (snapshot de l'état)
  loader.set_plugin_enabled(name, bool)          active/désactive un plugin
  loader.reload()                                re-scanne le dossier

Chaque élément de list_plugins() a la forme :
  {
    "name":        str,   # CommunityAddon.NAME
    "description": str,   # CommunityAddon.DESCRIPTION
    "version":     str,   # CommunityAddon.VERSION
    "author":      str,   # CommunityAddon.AUTHOR
    "module":      str,   # nom du fichier .py (sans extension)
    "enabled":     bool,
    "error":       str | None,  # message si le chargement a échoué
  }
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from .community import CommunityAddon
from ...utils import log_buffer

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Dossier scanné par le loader.
COMMUNITY_DIR = Path(__file__).parent / "community"

# Fichiers à ignorer lors du scan (package init + template).
_SKIP_MODULES = {"__init__", "example_addon"}


class _PluginEntry:
    """État interne d'un plugin découvert."""

    def __init__(self, module_name: str) -> None:
        self.module_name: str = module_name       # nom du .py sans extension
        self.instance: CommunityAddon | None = None
        self.enabled: bool = False
        self.error: str | None = None             # message d'erreur si import raté

    def to_dict(self) -> dict:
        inst = self.instance
        if inst is not None:
            return {
                "name": inst.NAME,
                "description": inst.DESCRIPTION,
                "version": inst.VERSION,
                "author": inst.AUTHOR,
                "module": self.module_name,
                "enabled": self.enabled,
                "error": self.error,
            }
        return {
            "name": self.module_name,
            "description": "",
            "version": "",
            "author": "",
            "module": self.module_name,
            "enabled": False,
            "error": self.error,
        }


class PluginLoader:
    """Gestionnaire de cycle de vie des addons communautaires."""

    def __init__(self, cache_manager=None, proxy_master=None) -> None:
        self._cache_manager = cache_manager
        self._proxy_master = proxy_master
        self._lock = threading.Lock()
        # module_name → _PluginEntry
        self._entries: dict[str, _PluginEntry] = {}

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def set_cache_manager(self, cache_manager) -> None:
        """Injecte le CacheManager (peut être appelé après __init__)."""
        with self._lock:
            self._cache_manager = cache_manager

    def set_proxy_master(self, proxy_master) -> None:
        """Injecte le ProxyMaster (peut être appelé après __init__)."""
        with self._lock:
            self._proxy_master = proxy_master

    def load_all(self) -> None:
        """Scanne le dossier community/ et charge tous les addons détectés."""
        COMMUNITY_DIR.mkdir(parents=True, exist_ok=True)
        found = sorted(
            p.stem for p in COMMUNITY_DIR.glob("*.py")
            if p.stem not in _SKIP_MODULES and not p.stem.startswith("_")
        )
        log_buffer.log("PluginLoader", f"Scan de {COMMUNITY_DIR.name}/ : {len(found)} fichier(s) trouvé(s)")
        for module_name in found:
            self._load_one(module_name)

    def reload(self) -> None:
        """Re-scanne le dossier et charge les nouveaux fichiers.

        Les plugins déjà chargés ne sont pas rechargés (pour éviter les
        instances fantômes). Pour recharger un plugin existant, l'utilisateur
        doit redémarrer Fleasion.
        """
        COMMUNITY_DIR.mkdir(parents=True, exist_ok=True)
        found = sorted(
            p.stem for p in COMMUNITY_DIR.glob("*.py")
            if p.stem not in _SKIP_MODULES and not p.stem.startswith("_")
        )
        with self._lock:
            known = set(self._entries.keys())
        new_modules = [m for m in found if m not in known]
        if new_modules:
            log_buffer.log("PluginLoader", f"Nouveaux addons détectés : {new_modules}")
        for module_name in new_modules:
            self._load_one(module_name)

    # ------------------------------------------------------------------
    # Chargement d'un seul module
    # ------------------------------------------------------------------

    def _load_one(self, module_name: str) -> None:
        entry = _PluginEntry(module_name)
        try:
            addon_instance = self._import_and_instantiate(module_name)
            entry.instance = addon_instance
            entry.enabled = True
            # Enregistrement auprès du CacheManager
            self._register_cache_listener(addon_instance)
            # Enregistrement auprès du ProxyMaster
            self._register_interceptor(addon_instance)
            # Hook de chargement
            try:
                addon_instance.on_load()
            except Exception as exc:
                log_buffer.log("PluginLoader", f"[{module_name}] on_load() a levé une exception : {exc}")
            log_buffer.log(
                "PluginLoader",
                f"Addon chargé : {addon_instance.NAME} v{addon_instance.VERSION} "
                f"par {addon_instance.AUTHOR} ({module_name}.py)",
            )
        except Exception as exc:
            entry.error = str(exc)
            log_buffer.log("PluginLoader", f"[{module_name}] Erreur de chargement : {exc}")
            logger.exception("PluginLoader: failed to load %s", module_name)

        with self._lock:
            self._entries[module_name] = entry

    def _import_and_instantiate(self, module_name: str) -> CommunityAddon:
        """Importe le fichier .py et retourne une instance de la première CommunityAddon trouvée."""
        py_path = COMMUNITY_DIR / f"{module_name}.py"
        if not py_path.exists():
            raise FileNotFoundError(f"Fichier introuvable : {py_path}")

        # Nom complet du module Python pour éviter les collisions.
        full_module_name = f"fleasion_community_addon_{module_name}"

        spec = importlib.util.spec_from_file_location(full_module_name, py_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Impossible de créer un spec pour {py_path}")

        mod = importlib.util.module_from_spec(spec)
        sys.modules[full_module_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]

        # Chercher la première sous-classe concrète de CommunityAddon dans le module.
        cls = None
        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, CommunityAddon)
                and obj is not CommunityAddon
                and obj.__module__ == full_module_name
            ):
                cls = obj
                break

        if cls is None:
            raise ImportError(
                f"{module_name}.py ne contient aucune classe héritant de CommunityAddon."
            )

        return cls()

    # ------------------------------------------------------------------
    # Enregistrement / désenregistrement
    # ------------------------------------------------------------------

    def _register_cache_listener(self, instance: CommunityAddon) -> None:
        cm = self._cache_manager
        if cm is None:
            return
        try:
            cm.add_new_asset_listener(self._make_cache_callback(instance))
        except Exception as exc:
            log_buffer.log("PluginLoader", f"Impossible d'enregistrer le listener cache pour {instance.NAME}: {exc}")

    def _make_cache_callback(self, instance: CommunityAddon):
        """Retourne un wrapper qui vérifie is_enabled avant d'appeler on_asset_cached."""
        def _cb(asset_id, asset_type, is_new, asset_entry):
            if not instance.is_enabled:
                return
            try:
                instance.on_asset_cached(asset_id, asset_type, is_new, asset_entry)
            except Exception as exc:
                log_buffer.log(
                    "PluginLoader",
                    f"[{instance.NAME}] on_asset_cached error : {exc}",
                )
        return _cb

    def _register_interceptor(self, instance: CommunityAddon) -> None:
        pm = self._proxy_master
        if pm is None:
            return
        try:
            pm.register_module_interceptor(_InterceptorWrapper(instance))
        except Exception as exc:
            log_buffer.log("PluginLoader", f"Impossible d'enregistrer l'intercepteur proxy pour {instance.NAME}: {exc}")

    def _unregister_interceptor(self, instance: CommunityAddon) -> None:
        pm = self._proxy_master
        if pm is None:
            return
        # On cherche le wrapper correspondant dans la liste des intercepteurs.
        try:
            with pm._lock:
                pm._module_interceptors = [
                    i for i in pm._module_interceptors
                    if not (isinstance(i, _InterceptorWrapper) and i.addon is instance)
                ]
                interceptors = list(pm._module_interceptors)
            if pm._proxy is not None:
                pm._proxy.set_module_interceptors(interceptors)
        except Exception as exc:
            log_buffer.log("PluginLoader", f"Impossible de désenregistrer l'intercepteur de {instance.NAME}: {exc}")

    # ------------------------------------------------------------------
    # API publique pour la GUI
    # ------------------------------------------------------------------

    def list_plugins(self) -> list[dict]:
        """Retourne une snapshot de l'état de tous les plugins détectés."""
        with self._lock:
            return [entry.to_dict() for entry in self._entries.values()]

    def set_plugin_enabled(self, module_name: str, enabled: bool) -> None:
        """Active ou désactive un plugin par nom de module."""
        with self._lock:
            entry = self._entries.get(module_name)
        if entry is None or entry.instance is None:
            return
        instance = entry.instance
        if entry.enabled == enabled:
            return
        entry.enabled = enabled
        instance.set_enabled(enabled)
        if not enabled:
            # Désactiver = retirer de la liste des intercepteurs proxy
            # (le callback cache vérifie is_enabled lui-même, pas besoin de le retirer)
            self._unregister_interceptor(instance)
            log_buffer.log("PluginLoader", f"Addon désactivé : {instance.NAME}")
        else:
            # Réactiver = ré-enregistrer comme intercepteur
            self._register_interceptor(instance)
            log_buffer.log("PluginLoader", f"Addon activé : {instance.NAME}")


class _InterceptorWrapper:
    """Wrappeur fin qui gate les appels request/response par is_enabled."""

    __slots__ = ("addon",)

    def __init__(self, addon: CommunityAddon) -> None:
        self.addon = addon

    def request(self, flow) -> None:
        if not self.addon.is_enabled:
            return
        try:
            self.addon.request(flow)
        except Exception as exc:
            log_buffer.log("PluginLoader", f"[{self.addon.NAME}] request() error : {exc}")

    def response(self, flow) -> None:
        if not self.addon.is_enabled:
            return
        try:
            self.addon.response(flow)
        except Exception as exc:
            log_buffer.log("PluginLoader", f"[{self.addon.NAME}] response() error : {exc}")
