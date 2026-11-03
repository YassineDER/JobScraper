"""Tests des exigences d'expérience et de leur utilisation dans LinkedIn."""

import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from click.testing import CliRunner

from jobscraper.cli import main
from jobscraper.models.job import ExperienceLevel, JobOffer
from jobscraper.scrapers.linkedin import LinkedInScraper
from jobscraper.utils.experience import experience_level_from_description


class ExperienceTests(unittest.TestCase):
    def test_experience_phrases(self):
        cases = {
            "Au moins 1 an d'expérience en Python": ExperienceLevel.JUNIOR,
            "Experience: 0-1 years": ExperienceLevel.JUNIOR,
            "2 à 3 ans d'expérience minimum": ExperienceLevel.JUNIOR,
            "Expérience professionnelle de deux années": ExperienceLevel.JUNIOR,
            "2,5 ans d'expérience": ExperienceLevel.JUNIOR,
            "3 ans d'expérience": ExperienceLevel.SENIOR,
            "3/5 ans d'expérience": ExperienceLevel.SENIOR,
            "Vous disposez de plus de 4 ans d’expérience": ExperienceLevel.SENIOR,
            "4 ans minimum d'expérience": ExperienceLevel.SENIOR,
            "Minimum 8+ years of hands-on experience": ExperienceLevel.SENIOR,
            "Expérience d'au moins cinq années": ExperienceLevel.SENIOR,
            "7 à 8 ans d'expérience, dont au moins 4 à 5 ans sur Flutter": ExperienceLevel.SENIOR,
            "Depuis 30 ans, notre société innove.": None,
            "5 ans de congés supplémentaires": None,
            "2 ans d'ancienneté donnent droit à des congés. Expérience en Python.": None,
            "Bac + 5 et expérience Python": None,
            "Une expérience en Python est appréciée": None,
            None: None,
        }
        for description, expected in cases.items():
            with self.subTest(description=description):
                self.assertEqual(experience_level_from_description(description), expected)

    def test_detail_page_prefers_description_and_preserves_inline_spaces(self):
        soup = BeautifulSoup(
            '<div class="description__text">Minimum <b>5 ans</b> '
            "d’expérience en Python</div>"
            '<li class="description__job-criteria-item">Entry level</li>',
            "lxml",
        )
        job = LinkedInScraper()._parse_job_details(
            soup, "123", "https://www.linkedin.com/jobs/view/123"
        )
        self.assertIsNotNone(job)
        self.assertEqual(job.experience_level, ExperienceLevel.SENIOR)
        self.assertIn("5 ans", job.description)

    def test_junior_cli_fetches_details_and_keeps_unknowns(self):
        def offer(identifier, level=None):
            return JobOffer(
                id=f"linkedin_{identifier}", source="linkedin",
                url=f"https://www.linkedin.com/jobs/view/{identifier}",
                title=f"Job {identifier}", company="Company", location="Paris",
                experience_level=level,
            )

        cards = [offer("1"), offer("2"), offer("3")]
        details = {"1": offer("1", ExperienceLevel.JUNIOR),
                   "2": offer("2", ExperienceLevel.SENIOR), "3": None}
        with patch("jobscraper.cli.LinkedInScraper") as scraper_class:
            scraper = scraper_class.return_value.__enter__.return_value
            scraper.search.return_value = iter(cards)
            scraper.get_job_details.side_effect = lambda identifier: details[identifier]
            result = CliRunner().invoke(main, ["search", "-s", "linkedin", "-e", "junior"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(scraper.get_job_details.call_count, 3)
        self.assertIn("Job 1", result.output)
        self.assertNotIn("Job 2", result.output)
        self.assertIn("Job 3", result.output)


if __name__ == "__main__":
    unittest.main()
