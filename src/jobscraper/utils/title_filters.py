"""Filtres d'intitulés partagés entre les sources d'offres."""

import re
import unicodedata


JUNIOR_EXCLUDED_TITLE_WORDS = {
    "senior", "expert", "experte", "experts", "expertes", "c++", "stage",
}


def junior_title_is_excluded(title: str) -> bool:
    """Exclut les mots demandés du titre, sans inspecter la description."""
    decomposed = unicodedata.normalize("NFKD", title.casefold())
    normalized = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    words = set(re.findall(r"[a-z0-9+#.]+", normalized))
    return bool(words & JUNIOR_EXCLUDED_TITLE_WORDS)
