"""example_addon.py — Template de départ pour les addons communautaires Fleasion.

Copiez ce fichier, renommez-le (par ex. "my_addon.py"), et éditez-le.
Fleasion le chargera automatiquement au prochain démarrage du proxy.

Documentation complète : https://github.com/fleasion/Fleasion
"""

from __future__ import annotations

import threading

# Import de la classe de base depuis le package community.
from . import CommunityAddon


class ExampleAddon(CommunityAddon):
    """Addon d'exemple : logue chaque nouvel asset image dans les logs Fleasion."""

    NAME = "Example Addon"
    DESCRIPTION = "Logue les nouvelles images interceptées. Utile comme template."
    VERSION = "1.0.0"
    AUTHOR = "fleasion-community"

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._count = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self) -> None:
        """Appelé une seule fois après le chargement de l'addon."""
        # Importez log_buffer ici (pas en tête de module) pour éviter les
        # imports circulaires si votre addon est chargé très tôt.
        from ....utils.logging import log_buffer
        log_buffer.log("ExampleAddon", f"{self.NAME} v{self.VERSION} chargé !")

    # ------------------------------------------------------------------
    # Hook cache : appelé dès qu'un asset est mis en cache
    # ------------------------------------------------------------------

    def on_asset_cached(
        self,
        asset_id: str,
        asset_type: int,
        is_new: bool,
        asset_entry: dict,
    ) -> None:
        # On ne s'intéresse qu'aux *nouveaux* assets de type Image (1) ou Decal (13).
        if not is_new or asset_type not in (1, 13):
            return

        from ....utils.logging import log_buffer

        with self._lock:
            self._count += 1
            count = self._count

        size = asset_entry.get("size", 0)
        log_buffer.log(
            "ExampleAddon",
            f"Nouvelle image #{count} : asset_id={asset_id}, taille={size} bytes",
        )

    # ------------------------------------------------------------------
    # Hook proxy : appelé pour chaque requête gamejoin (optionnel)
    # ------------------------------------------------------------------

    def request(self, flow) -> None:
        # Exemple : on logue l'URL de chaque requête gamejoin.
        # En production, commentez ou supprimez ce bloc pour éviter le spam.
        # from ....utils.logging import log_buffer
        # log_buffer.log("ExampleAddon", f"Request: {flow.request.pretty_url}")
        pass

    def response(self, flow) -> None:
        # Exemple : on pourrait inspecter flow.response.json() ici.
        pass