"""Tests du scraper Welcome to the Jungle."""

from datetime import datetime, timezone
from typing import Optional

from jobscraper.models.job import (
    ContractType,
    DatePosted,
    ExperienceLevel,
    SearchCriteria,
    SortBy,
    WorkplaceType,
)
from jobscraper.scrapers.wttj import WTTJScraper


def _hit(
    identifier: str,
    title: str,
    contract_type: str = "full_time",
    experience: Optional[float] = None,
) -> dict:
    return {
        "reference": identifier,
        "name": title,
        "slug": f"job-{identifier}",
        "summary": "Description du poste",
        "contract_type": contract_type,
        "experience_level_minimum": experience,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "organization": {"name": "ACME", "slug": "acme"},
        "offices": [{"city": "Paris", "country": "France"}],
    }


def test_contract_mapping_distinguishes_cdd_and_interim() -> None:
    scraper = WTTJScraper()

    cdd = scraper._parse_hit(_hit("cdd", "Développeur en CDD", "temporary"))
    interim = scraper._parse_hit(
        _hit("interim", "Développeur - mission intérim", "temporary")
    )
    part_time = scraper._parse_hit(_hit("part", "Assistant", "part_time"))
    vie = scraper._parse_hit(_hit("vie", "Analyste V.I.E.", "vie"))

    assert cdd is not None and cdd.contract_type == ContractType.CDD
    assert interim is not None and interim.contract_type == ContractType.INTERIM
    assert part_time is not None and part_time.contract_type == ContractType.OTHER
    assert vie is not None and vie.contract_type == ContractType.OTHER


def test_build_filters_applies_criteria_but_ignores_workplace() -> None:
    criteria = SearchCriteria(
        contract_types=[ContractType.CDD],
        experience_levels=[ExperienceLevel.SENIOR],
        date_posted=DatePosted.PAST_WEEK,
        companies=["ACME France"],
        exclude_companies=["Bad Company"],
        salary_min=50_000,
        workplace_types=[WorkplaceType.REMOTE],
    )

    filters = WTTJScraper()._build_filters(criteria)

    assert "contract_type:temporary" in filters
    assert "contract_type:fixed_term" in filters
    assert "experience_level_minimum>3" in filters
    assert "experience_level_minimum<=6" in filters
    assert "published_at_timestamp>=" in filters
    assert 'organization.name:"ACME France"' in filters
    assert 'NOT organization.name:"Bad Company"' in filters
    assert "salary_yearly_minimum>=50000" in filters
    assert "remote:" not in filters


def test_search_uses_date_index_and_removes_wrong_temporary_contracts(
    monkeypatch,
) -> None:
    scraper = WTTJScraper()
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "hits": [
                _hit("interim", "Mission intérim", "temporary"),
                _hit("cdd", "Développeur CDD", "temporary"),
            ],
            "nbPages": 1,
        }

    monkeypatch.setattr(scraper, "_fetch_algolia", fake_fetch)
    criteria = SearchCriteria(
        contract_types=[ContractType.CDD],
        sort_by=SortBy.DATE,
        max_results=10,
    )

    jobs = list(scraper.search(criteria))

    assert [job.id for job in jobs] == ["wttj_cdd"]
    assert calls[0][1]["index_name"] == scraper.ALGOLIA_DATE_INDEX


def test_experience_levels_are_parsed_and_filtered_locally() -> None:
    scraper = WTTJScraper()
    internship = scraper._parse_hit(_hit("stage", "Stage Python", "internship", 0))
    director = scraper._parse_hit(_hit("director", "Directeur technique", experience=12))

    assert internship is not None
    assert internship.experience_level == ExperienceLevel.INTERNSHIP
    assert director is not None
    assert director.experience_level == ExperienceLevel.DIRECTOR
    assert scraper._matches_criteria(
        director,
        SearchCriteria(experience_levels=[ExperienceLevel.DIRECTOR]),
    )
    assert not scraper._matches_criteria(
        director,
        SearchCriteria(experience_levels=[ExperienceLevel.JUNIOR]),
    )
