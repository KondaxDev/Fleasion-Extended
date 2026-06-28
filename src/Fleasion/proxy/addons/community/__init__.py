"""Community addons package.

Each .py file in this folder that defines a class inheriting from
``CommunityAddon`` is automatically discovered and loaded by
``PluginLoader`` at proxy startup.

See ``example_addon.py`` for a fully commented template.
"""

from __future__ import annotations

from typing import Optional


class CommunityAddon:
    """Base class for community-written Fleasion addons.

    Subclass this and override only the hooks you need.
    All methods are optional — a no-op base implementation is provided for
    each so you never have to implement hooks you don't use.

    Lifecycle
    ---------
    1. PluginLoader discovers every .py file in this directory.
    2. It imports the module and looks for a class that is a subclass of
       CommunityAddon (but not CommunityAddon itself).
    3. It calls ``cls()`` with no arguments to create an instance.
    4. The instance is registered:
         - as a new-asset listener with CacheManager (→ on_asset_cached)
         - as a module interceptor with ProxyMaster (→ request / response)
    5. If the user disables the addon from the UI, PluginLoader calls
       ``set_enabled(False)`` and unregisters it until re-enabled.

    Thread safety
    -------------
    ``on_asset_cached`` is called from a background thread; protect any
    shared state with a ``threading.Lock``.

    ``request`` / ``response`` are called from the proxy's async executor
    threads; same rule applies — use a lock for anything you mutate.
    """

    # Human-readable name shown in the Fleasion UI.
    # Override in your subclass.
    NAME: str = "Unnamed Addon"

    # One-line description shown as tooltip / subtitle in the UI.
    DESCRIPTION: str = ""

    # Version string (free-form, shown in the UI).
    VERSION: str = "1.0.0"

    # Author field shown in the UI.
    AUTHOR: str = "anonymous"

    def __init__(self) -> None:
        self._enabled: bool = True

    # ------------------------------------------------------------------
    # Internal helpers — do not override
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """Called by PluginLoader when the user toggles the addon on/off."""
        self._enabled = enabled
        self.on_enable() if enabled else self.on_disable()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Lifecycle hooks — override as needed
    # ------------------------------------------------------------------

    def on_load(self) -> None:
        """Called once after the addon has been instantiated and registered.

        Use this for one-time initialisation (e.g. loading a config file,
        creating a persistent HTTP session, etc.).
        """

    def on_enable(self) -> None:
        """Called each time the addon is enabled (including the first load)."""

    def on_disable(self) -> None:
        """Called each time the addon is disabled via the UI."""

    # ------------------------------------------------------------------
    # Cache hook
    # ------------------------------------------------------------------

    def on_asset_cached(
        self,
        asset_id: str,
        asset_type: int,
        is_new: bool,
        asset_entry: dict,
    ) -> None:
        """Called whenever the Cache Scraper stores an asset.

        Parameters
        ----------
        asset_id : str
            Roblox asset ID (numeric string, e.g. "7547298681").
        asset_type : int
            Roblox asset type ID (1=Image, 13=Decal, 3=Audio, 4=Mesh, …).
            See ``CacheManager.ASSET_TYPES`` for the full mapping.
        is_new : bool
            True when this asset was never in the local cache index before.
            False when a previously-seen asset is being refreshed.
        asset_entry : dict
            A copy of the index entry: keys include ``asset_type``,
            ``cached_at`` (ISO timestamp string), ``url``, ``size`` (bytes),
            and whatever metadata the scraper attached.

        Notes
        -----
        This runs in a **background thread** — do not touch Qt widgets here.
        If you need to update the UI, use ``QTimer.singleShot(0, callback)``
        or a PyQt signal.
        """

    # ------------------------------------------------------------------
    # Proxy hooks — only called when proxy is running
    # ------------------------------------------------------------------

    def request(self, flow) -> None:
        """Called for every intercepted HTTP request before it is forwarded.

        Currently triggered for ``gamejoin.roblox.com`` traffic only
        (same scope as the built-in UsernameSpoofer).

        Parameters
        ----------
        flow : ProxyFlow
            Lightweight flow object.  Relevant attributes:

            ``flow.request.pretty_url``  — full URL as string
            ``flow.request.headers``     — case-insensitive header accessor
                                           (get / set by string key)
            ``flow.request.raw_content`` — request body bytes (read/write)
            ``flow.request.url``         — URL (settable to redirect)
            ``flow.drop_request``        — set True to drop the request and
                                           return an empty 204 response
            ``flow.drop_status_code``    — HTTP status to use when dropping
            ``flow.drop_body``           — body bytes/str to use when dropping
        """

    def response(self, flow) -> None:
        """Called for every intercepted HTTP response before it reaches Roblox.

        Parameters
        ----------
        flow : ProxyFlow
            Same object passed to ``request()``, now with
            ``flow.response`` populated:

            ``flow.response.status_code`` — integer HTTP status
            ``flow.response.content``     — response body bytes (read-only)
            ``flow.response.json()``      — convenience JSON parse helper
        """