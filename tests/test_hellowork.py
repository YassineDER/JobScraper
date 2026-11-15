"""Tests hors réseau des exclusions junior et de la pagination HelloWork."""

import json
import unittest
from unittest.mock import patch

from jobscraper.models.job import ExperienceLevel, SearchCriteria
from jobscraper.scrapers.hellowork import HelloWorkScraper


def listing_page(titles):
    return "<ul>" + "".join(
        f'<li data-id-storage-item-id="{identifier}">'
        f'<input name="title" value="{title}">'
        '<input name="company" value="ACME"></li>'
        for identifier, title in titles
    ) + "</ul>"


def detail_page(title, qualifications="<p>Un an d'expérience en Python.</p>", months=12):
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "description": (
            "<h2>Les missions du poste</h2><p>Développer des applications.</p>"
            "<h2>Le profil recherché</h2>" + qualifications
        ),
        "qualifications": qualifications,
        "experienceRequirements": {"monthsOfExperience": months},
        "employmentType": ["TEMPORARY", "FULL_TIME"],
        "datePosted": "2026-11-10T00:56:24Z",
        "hiringOrganization": {"name": "Entreprise test"},
        "jobLocation": {
            "address": {"addressLocality": "Paris", "addressCountry": "FR"}
        },
    }
    return (
        '<html><script type="application/ld+json">'
        + json.dumps(posting, ensure_ascii=False)
        + "</script></html>"
    )


class HelloWorkJuniorTests(unittest.TestCase):
    def test_excluded_page_does_not_hide_later_junior_jobs(self):
        rejected = [
            ("1", "Développeur SENIOR"),
            ("2", "Développeuse experte Python"),
            ("3", "Développeur C/C++"),
            ("4", "STAGE - Développeur web"),
            ("5", "Développeur sénior Java"),
        ]
        accepted = [("6", "Développeur junior Python")]
        scraper = HelloWorkScraper({"delay": 0})
        with patch.object(
            scraper, "_fetch_page",
            side_effect=[
                listing_page(rejected), listing_page(accepted),
                detail_page("Développeur junior Python"),
            ],
        ) as fetch:
            jobs = list(scraper.search(SearchCriteria(
                experience_levels=[ExperienceLevel.JUNIOR], max_results=1,
            )))
        self.assertEqual([job.id for job in jobs], ["hellowork_6"])
        self.assertEqual(fetch.call_count, 3)
        self.assertIn("Développer des applications", jobs[0].description)

    def test_no_junior_filter_keeps_excluded_titles(self):
        titles = [("1", "Développeur Senior C++")]
        for levels in ([], [ExperienceLevel.SENIOR]):
            with self.subTest(levels=levels):
                scraper = HelloWorkScraper({"delay": 0})
                with patch.object(
                    scraper, "_fetch_page", side_effect=[
                        listing_page(titles),
                        detail_page(
                            "Développeur Senior C++",
                            "<p>Vous justifiez de 5 ans d'expérience en C++.</p>",
                        ),
                    ],
                ):
                    jobs = list(scraper.search(SearchCriteria(
                        experience_levels=levels, max_results=1,
                    )))
                self.assertEqual([job.id for job in jobs], ["hellowork_1"])

    def test_profile_overrides_misleading_numeric_experience(self):
        scraper = HelloWorkScraper({"delay": 0})
        with patch.object(
            scraper,
            "_fetch_page",
            return_value=detail_page(
                "Développeur Python",
                "<p>Vous justifiez de 5 ans d'expérience en Python.</p>",
                months=12,
            ),
        ) as fetch:
            job = scraper.get_job_details("hellowork_42")
            cached = scraper.get_job_details("hellowork_42")

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(job.experience_level, ExperienceLevel.SENIOR)
        self.assertEqual(cached.description, job.description)
        self.assertEqual(job.company, "Entreprise test")
        self.assertEqual(job.location, "Paris, France")
        self.assertEqual(job.contract_type.value, "cdd")
        self.assertIsNotNone(job.posted_at)

    def test_search_skips_non_junior_details_before_counting_results(self):
        scraper = HelloWorkScraper({"delay": 0})
        with patch.object(scraper, "_fetch_page", side_effect=[
            listing_page([("1", "Développeur Python"), ("2", "Développeur Java")]),
            detail_page(
                "Développeur Python",
                "<p>Vous justifiez de 5 ans d'expérience en Python.</p>",
            ),
            detail_page("Développeur Java"),
        ]) as fetch:
            jobs = list(scraper.search(SearchCriteria(
                experience_levels=[ExperienceLevel.JUNIOR], max_results=1,
            )))
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual([job.id for job in jobs], ["hellowork_2"])

    def test_missing_jobposting_is_not_exported_without_description(self):
        scraper = HelloWorkScraper({"delay": 0})
        with patch.object(scraper, "_fetch_page", side_effect=[
            listing_page([("1", "Développeur Python")]),
            "<html><title>Offre retirée</title></html>",
            "<html></html>",
        ]):
            jobs = list(scraper.search(SearchCriteria(max_results=1)))
        self.assertEqual(jobs, [])
        self.assertEqual(scraper.detail_failures, 1)


if __name__ == "__main__":
    unittest.main()
