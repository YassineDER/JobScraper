"""Interprétation prudente de l'expérience professionnelle demandée."""

import re
import unicodedata
from typing import Optional

from jobscraper.models.job import ExperienceLevel


_NUMBERS = {
    "zero": 0, "un": 1, "une": 1, "one": 1, "deux": 2, "two": 2,
    "trois": 3, "three": 3, "quatre": 4, "four": 4, "cinq": 5,
    "five": 5, "six": 6, "seven": 7, "sept": 7, "huit": 8,
    "eight": 8, "neuf": 9, "nine": 9, "dix": 10, "ten": 10,
}
_NUMBER = r"(?:\d{1,2}(?:[.,]\d+)?|" + "|".join(_NUMBERS) + r")"
_YEARS = r"(?:ans?|annees?|years?|yrs?)\b"
_DURATION = re.compile(
    rf"\b(?P<min>{_NUMBER})(?:\s*(?:a|to|[-–—/])\s*(?P<max>{_NUMBER}))?"
    rf"\s*(?:\+\s*)?{_YEARS}"
)
_EXPERIENCE = re.compile(r"\b(?:experience|experiences|experienced|expertise)\b")
_UNRELATED = re.compile(
    r"\b(?:conges?|vacances?|anciennete|retraite|fonde[es]?|existe|"
    r"entreprise|societe|company|founded|vacation|holidays?|leave|tenure)\b"
)


def experience_level_from_description(description: Optional[str]) -> Optional[ExperienceLevel]:
    """Déduit le niveau de l'exigence chiffrée, ou None si elle est ambiguë.

    Seules les durées proches d'une mention d'expérience sont retenues. Pour
    une fourchette, la borne basse est utilisée ; lorsque plusieurs exigences
    sont identifiées, la plus élevée prévaut.
    """
    if not description:
        return None
    normalized = unicodedata.normalize("NFKD", description.lower())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.replace("’", "'")
    values = []
    for match in _DURATION.finditer(normalized):
        # Une phrase ou une puce voisine peut concerner un avantage sans lien.
        left = max(normalized.rfind(char, 0, match.start()) for char in "\n;.!?") + 1
        right_candidates = [normalized.find(char, match.end()) for char in "\n;.!?"]
        right = min((pos for pos in right_candidates if pos >= 0), default=len(normalized))
        before = normalized[max(left, match.start() - 65):match.start()]
        after = normalized[match.end():min(right, match.end() + 65)]
        nearby = before + " " + after
        if not _EXPERIENCE.search(nearby):
            continue
        # Les mentions explicites d'ancienneté/avantages ne sont pas des prérequis.
        if _UNRELATED.search(before[-35:]) or _UNRELATED.search(after[:35]):
            continue
        token = match.group("min")
        value = _NUMBERS.get(token)
        if value is None:
            value = float(token.replace(",", "."))
        values.append(value)
    if not values:
        return None
    years = max(values)
    if years < 3:
        return ExperienceLevel.JUNIOR
    return ExperienceLevel.SENIOR
