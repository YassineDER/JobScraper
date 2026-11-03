"""Tests du filtre d'activité des candidatures LinkedIn."""

import pytest
from bs4 import BeautifulSoup

from jobscraper.scrapers.linkedin import LinkedInScraper


@pytest.mark.parametrize(
    "text",
    [
        "Over 100 applicants",
        "Over 100 people clicked apply",
        "More than 100 applicants",
        "100+ applicants",
        "Plus de 100 candidatures",
        "Plus de 100 personnes ont cliqué sur Postuler",
        "Plus de 100 personnes ont cliqué pour postuler",
        "245 applicants",
        "245 candidats",
    ],
)
def test_detects_over_100_application_activity(text: str) -> None:
    assert LinkedInScraper.is_over_100_application_activity(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "70 applicants",
        "70 candidats",
        "Be among the first 25 applicants",
        "Faites partie des 25 premiers candidats",
        "Posted 100 days ago",
    ],
)
def test_keeps_jobs_without_over_100_application_activity(text: str) -> None:
    assert not LinkedInScraper.is_over_100_application_activity(text)


def test_extracts_application_activity_from_public_linkedin_markup() -> None:
    soup = BeautifulSoup(
        '<span class="num-applicants__caption">Over 100 applicants</span>',
        "lxml",
    )

    element = soup.select_one(LinkedInScraper.APPLICATION_ACTIVITY_SELECTOR)

    assert element is not None
    assert element.get_text(" ", strip=True) == "Over 100 applicants"
