"""Creator Watcher - notify a Discord webhook when a watched creator (user or
group) publishes a NEW asset that Fleasion's cache scraper intercepts.

Designed to plug into CacheManager.add_new_asset_listener(...) so it gets
called for every freshly-cached asset (image, mesh, audio, gamepass icon,
etc.) without changing the scraper's hot path.

Storage (under CONFIG_DIR):
    creator_watch.json       - list of watch entries the user configured
    creator_watch_seen.json  - per-creator set of asset IDs already notified
                               (capped so it doesn't grow forever)
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from ...utils import log_buffer
from ...utils.paths import CONFIG_DIR

WATCH_FILE = CONFIG_DIR / "creator_watch.json"
SEEN_FILE = CONFIG_DIR / "creator_watch_seen.json"

# Roblox asset type IDs that are worth telling someone about; everything else
# (fonts, internal metadata, etc.) is skipped to avoid spamming the webhook.
_NOTIFY_ASSET_TYPES = {
    1: "Image", 13: "Decal", 2: "T-Shirt", 11: "Shirt", 12: "Pants",
    9: "Hat", 8: "Head", 17: "Head", 18: "Face", 19: "Gear",
    27: "Torso", 28: "RightArm", 29: "LeftArm", 30: "LeftLeg", 31: "RightLeg",
    32: "Package", 38: "Hat", 41: "HairAccessory", 42: "FaceAccessory",
    43: "NeckAccessory", 44: "ShoulderAccessory", 45: "FrontAccessory",
    46: "BackAccessory", 47: "WaistAccessory", 48: "ClimbAnimation",
    61: "Animation", 62: "Arms", 63: "TexturePack", 64: "TShirtAccessory",
    65: "ShirtAccessory", 66: "PantsAccessory", 78: "MoodAnimation",
    79: "DynamicHead", 80: "CreatedPlace",
}

_MAX_SEEN_PER_CREATOR = 500


def _load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path, data):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        log_buffer.log("CreatorWatch", f"Failed to persist {path.name}: {exc}")


class CreatorWatcher:
    """Watches a configurable list of Roblox creators and posts a Discord
    webhook message whenever they publish (i.e. Fleasion caches) a new asset.
    """

    def __init__(self, cache_scraper=None) -> None:
        # cache_scraper is used to resolve an asset's creator via the
        # creator-info lookup it already implements (develop.roblox.com).
        self._cache_scraper = cache_scraper
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="creator_watch")

        self._watches: list[dict] = _load_json(WATCH_FILE, [])
        self._seen: dict[str, list[str]] = _load_json(SEEN_FILE, {})

    # -- persistence -----------------------------------------------------

    def list_watches(self) -> list[dict]:
        with self._lock:
            return [dict(w) for w in self._watches]

    def add_watch(self, creator_id: int, creator_type: int, label: str, webhook_url: str) -> None:
        with self._lock:
            self._watches.append({
                "creator_id": int(creator_id),
                "creator_type": int(creator_type),  # 1 = User, 2 = Group
                "label": label or str(creator_id),
                "webhook_url": webhook_url,
                "added_at": time.time(),
            })
            _save_json(WATCH_FILE, self._watches)

    def remove_watch(self, index: int) -> None:
        with self._lock:
            if 0 <= index < len(self._watches):
                self._watches.pop(index)
                _save_json(WATCH_FILE, self._watches)

    def _mark_seen(self, creator_id: int, asset_id: str) -> bool:
        """Returns True if this is the first time we've seen asset_id for creator_id."""
        key = str(creator_id)
        with self._lock:
            seen_list = self._seen.setdefault(key, [])
            if asset_id in seen_list:
                return False
            seen_list.append(asset_id)
            if len(seen_list) > _MAX_SEEN_PER_CREATOR:
                del seen_list[: len(seen_list) - _MAX_SEEN_PER_CREATOR]
            _save_json(SEEN_FILE, self._seen)
            return True

    # -- main hook, called by CacheManager.store_asset() ------------------

    def on_new_asset(self, asset_id: str, asset_type: int, is_new: bool, asset_entry: dict) -> None:
        if not is_new:
            return
        with self._lock:
            if not self._watches:
                return
        if asset_type not in _NOTIFY_ASSET_TYPES:
            return
        try:
            self._executor.submit(self._resolve_and_notify, str(asset_id), asset_type)
        except RuntimeError:
            pass

    def _resolve_and_notify(self, asset_id: str, asset_type: int) -> None:
        if self._cache_scraper is None:
            return
        try:
            creator_id, creator_type = self._cache_scraper._fetch_creator_info(asset_id)
        except Exception as exc:
            log_buffer.log("CreatorWatch", f"Creator lookup failed for {asset_id}: {exc}")
            return
        if creator_id is None:
            return

        with self._lock:
            matches = [w for w in self._watches if int(w.get("creator_id", -1)) == creator_id]
        if not matches:
            return

        if not self._mark_seen(creator_id, asset_id):
            return  # already notified for this creator/asset combo

        type_name = _NOTIFY_ASSET_TYPES.get(asset_type, str(asset_type))
        for watch in matches:
            self._post_webhook(watch, asset_id, type_name)

    def _post_webhook(self, watch: dict, asset_id: str, type_name: str) -> None:
        webhook_url = watch.get("webhook_url")
        if not webhook_url:
            return
        label = watch.get("label") or str(watch.get("creator_id"))
        asset_url = f"https://www.roblox.com/library/{asset_id}/"
        payload = {
            "username": "Fleasion Creator Watch",
            "embeds": [
                {
                    "title": f"Nouvel asset de {label}",
                    "description": f"Type: **{type_name}**\nAsset ID: `{asset_id}`",
                    "url": asset_url,
                    "color": 0x5865F2,
                    "thumbnail": {
                        "url": f"https://www.roblox.com/asset-thumbnail/image?assetId={asset_id}&width=420&height=420&format=png"
                    },
                }
            ],
        }
        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            if resp.status_code not in (200, 204):
                log_buffer.log(
                    "CreatorWatch",
                    f"Webhook for '{label}' returned HTTP {resp.status_code} (asset {asset_id})",
                )
            else:
                log_buffer.log("CreatorWatch", f"Notified '{label}' webhook for new asset {asset_id} ({type_name})")
        except Exception as exc:
            log_buffer.log("CreatorWatch", f"Webhook send failed for '{label}': {exc}")


def resolve_creator_for_input(raw: str) -> tuple[int | None, int | None, str | None]:
    """Best-effort resolve a username, group name, or numeric ID + type to
    (creator_id, creator_type, resolved_label). Tries user lookup first, then group.
    creator_type: 1 = User, 2 = Group.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None, None

    sess = requests.Session()
    sess.trust_env = False
    sess.proxies = {}

    # Plain numeric ID with no way to know type — try user, then group.
    if raw.isdigit():
        try:
            resp = sess.get(f"https://users.roblox.com/v1/users/{raw}", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return int(raw), 1, data.get("name", raw)
        except Exception:
            pass
        try:
            resp = sess.get(f"https://groups.roblox.com/v1/groups/{raw}", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return int(raw), 2, data.get("name", raw)
        except Exception:
            pass
        return int(raw), 1, raw

    # Username lookup
    try:
        resp = sess.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [raw], "excludeBannedUsers": False},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                return int(data[0]["id"]), 1, data[0].get("name", raw)
    except Exception:
        pass

    # Group name search fallback
    try:
        resp = sess.get(
            "https://groups.roblox.com/v1/groups/search",
            params={"keyword": raw, "limit": 10},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                return int(data[0]["id"]), 2, data[0].get("name", raw)
    except Exception:
        pass

    return None, None, None
