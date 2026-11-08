"""Tests du scraper Welcome to the Jungle."""

import json
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from typing import Optional
import unittest
from unittest.mock import Mock, patch

import requests
from pydantic import HttpUrl

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
    summary: str = "Description du poste",
) -> dict:
    return {
        "reference": identifier,
        "name": title,
        "slug": f"job-{identifier}",
        "summary": summary,
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


def test_build_filters_applies_france_but_ignores_experience_salary_and_workplace() -> None:
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
    assert "experience_level_minimum" not in filters
    assert "offices.country_code:FR" in filters
    assert "published_at_timestamp>=" in filters
    assert 'organization.name:"ACME France"' in filters
    assert 'NOT organization.name:"Bad Company"' in filters
    assert "salary_yearly_minimum" not in filters
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
    monkeypatch.setattr(
        scraper, "get_job_details", lambda identifier: scraper._known_jobs[identifier]
    )
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


def test_description_experience_overrides_numeric_field() -> None:
    scraper = WTTJScraper()
    junior = scraper._parse_hit(
        _hit(
            "junior",
            "Développeur Python",
            experience=5,
            summary="Nous recherchons une personne avec 2 ans d'expérience en Python.",
        )
    )
    senior = scraper._parse_hit(
        _hit(
            "senior",
            "Développeur Python",
            experience=0,
            summary="Minimum 5 ans d'expérience en Python requis.",
        )
    )
    unknown = scraper._parse_hit(_hit("unknown", "Développeur Python"))

    assert junior is not None and junior.experience_level == ExperienceLevel.JUNIOR
    assert senior is not None and senior.experience_level == ExperienceLevel.SENIOR
    assert unknown is not None
    assert not scraper._matches_criteria(
        unknown, SearchCriteria(experience_levels=[ExperienceLevel.JUNIOR])
    )
    assert not scraper._matches_criteria(
        senior, SearchCriteria(experience_levels=[ExperienceLevel.JUNIOR])
    )


def test_france_office_is_displayed_for_multi_country_job() -> None:
    hit = _hit("multiple", "Développeur Python")
    hit["offices"] = [
        {"city": "Milan", "country": "Italy", "country_code": "IT"},
        {"city": "Paris", "country": "France", "country_code": "FR"},
    ]

    job = WTTJScraper()._parse_hit(hit, prefer_france=True)

    assert job is not None and job.location == "Paris, France"


def _detail_payload(identifier="detail", years=2):
    return {
        "job": {
            "wttj_reference": identifier,
            "name": "Développeur Java",
            "slug": f"job-{identifier}",
            "status": "published",
            "archived_at": None,
            "contract_type": "full_time",
            "description": (
                "<h2>Notre entreprise</h2>"
                "<p>Un groupe avec 35 ans d'expérience dans le logiciel.</p>"
                "<h2>Votre profil :</h2>"
                f"<p>Expérience requise : <b>{years} ans</b> en Java.</p>"
            ),
            "profile": None,
            "recruitment_process": "<p>Entretien technique.</p>",
            "organization": {"name": "ACME"},
            "offices": [{"city": "Paris", "country_code": "FR"}],
            "published_at": "2026-11-01T12:00:00Z",
        }
    }


class WTTJDetailsTests(unittest.TestCase):
    def setUp(self):
        self.scraper = WTTJScraper({"delay": 0})
        self.addCleanup(self.scraper.close)

    def response(self, payload, status=200):
        response = Mock(status_code=status)
        response.json.return_value = payload
        return response

    def test_full_description_is_fetched_cached_and_classifies_candidate(self):
        url = "https://www.welcometothejungle.com/fr/companies/acme/jobs/job-detail"
        response = self.response(_detail_payload())
        with patch.object(self.scraper._session, "get", return_value=response) as fetch:
            job = self.scraper.get_job_details(url)
            cached = self.scraper.get_job_details(url)

        self.assertIsNotNone(job)
        self.assertEqual(job.experience_level, ExperienceLevel.JUNIOR)
        self.assertIn("2 ans", job.description)
        self.assertIn("Entretien technique", job.description)
        self.assertNotIn("<b>", job.description)
        self.assertEqual(job.location, "Paris, France")
        self.assertEqual(str(job.url), url)
        self.assertEqual(cached, job)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(
            fetch.call_args.args[0],
            "https://api.welcometothejungle.com/api/v1/organizations/acme/jobs/job-detail",
        )

    def test_profile_overrides_company_history_and_listing_metadata(self):
        payload = _detail_payload()["job"]
        payload["description"] = "<p>Un groupe avec 35 ans d'expérience.</p>"
        payload["profile"] = "<p>Minimum <b>deux années</b> d'expérience.</p>"
        listing = self.scraper._parse_hit(_hit("original-id", "Java", experience=8))

        job = self.scraper._parse_job_details(payload, "acme", "job-detail", listing)

        self.assertEqual(job.id, listing.id)
        self.assertEqual(job.experience_level, ExperienceLevel.JUNIOR)
        self.assertIn("deux années", job.description)

    def test_pagination_counts_matches_not_candidates_and_skips_duplicates(self):
        first = _hit("senior", "Java", experience=0)
        second = _hit("junior", "Java", experience=8)
        pages = [
            {"hits": [first, first], "nbPages": 2},
            {"hits": [second], "nbPages": 2},
        ]
        with patch.object(self.scraper, "_fetch_algolia", side_effect=pages) as search:
            with patch.object(
                self.scraper._session,
                "get",
                side_effect=[
                    self.response(_detail_payload("senior", 5)),
                    self.response(_detail_payload("junior", 2)),
                ],
            ) as details:
                jobs = list(self.scraper.search(SearchCriteria(
                    keywords=["Java"],
                    experience_levels=[ExperienceLevel.JUNIOR],
                    max_results=1,
                )))

        self.assertEqual([job.id for job in jobs], ["wttj_junior"])
        self.assertEqual(jobs[0].experience_level, ExperienceLevel.JUNIOR)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(details.call_count, 2)

    def test_failed_details_are_not_returned_as_summary_only_matches(self):
        hit = _hit("failed", "Java", experience=0)
        with patch.object(self.scraper, "_fetch_algolia", return_value={
            "hits": [hit, _hit("passed", "Java")], "nbPages": 1,
        }):
            with patch.object(
                self.scraper._session, "get", side_effect=[
                    requests.Timeout("timeout"), self.response(_detail_payload("passed")),
                ]
            ):
                jobs = list(self.scraper.search(SearchCriteria(max_results=1)))

        self.assertEqual([job.id for job in jobs], ["wttj_passed"])
        self.assertEqual(self.scraper.detail_failures, 1)

    def test_removed_archived_empty_and_malformed_details_are_rejected(self):
        archived = _detail_payload()
        archived["job"]["status"] = "archived"
        empty = _detail_payload()
        empty["job"]["description"] = ""
        responses = [
            self.response({}, status=404),
            self.response(archived),
            self.response(empty),
            self.response({"unexpected": "schema"}),
        ]
        with patch.object(self.scraper._session, "get", side_effect=responses):
            for index in range(4):
                self.assertIsNone(self.scraper.get_job_details(
                    f"https://www.welcometothejungle.com/fr/companies/acme/jobs/job-{index}"
                ))

        self.assertEqual(self.scraper.unavailable_jobs, 2)
        self.assertEqual(self.scraper.detail_failures, 2)

    def test_experience_unknown_after_details_does_not_satisfy_filter(self):
        payload = _detail_payload()["job"]
        payload["description"] = "<p>Développer nos applications Java.</p>"
        job = self.scraper._parse_job_details(payload, "acme", "job-detail")

        self.assertIsNone(job.experience_level)
        self.assertFalse(self.scraper._matches_criteria(
            job, SearchCriteria(experience_levels=[ExperienceLevel.JUNIOR])
        ))
        self.assertTrue(self.scraper._matches_criteria(job, SearchCriteria()))

    def test_json_export_keeps_full_url_and_description_intact(self):
        from jobscraper.cli import save_jobs_json

        job = self.scraper._parse_job_details(_detail_payload()["job"], "acme", "job-detail")
        long_slug = "developpeur-fullstack-java_grenoble_THALE_m7V7PMV"
        url = f"https://www.welcometothejungle.com/fr/companies/thales/jobs/{long_slug}"
        job.url = HttpUrl(url)
        job.description = "[bold]Texte littéral[/bold] " + "description longue " * 30
        output = StringIO()

        with redirect_stdout(output):
            save_jobs_json([job], None)

        exported = json.loads(output.getvalue())[0]
        self.assertEqual(exported["url"], url)
        self.assertEqual(exported["description"], job.description)


if __name__ == "__main__":
    unittest.main()
