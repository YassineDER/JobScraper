"""Scraper pour LinkedIn Jobs."""

import re
import time
import unicodedata
from datetime import datetime, timedelta
from typing import Dict, Iterator, Optional
from urllib.parse import quote_plus, urlencode

from bs4 import BeautifulSoup
from loguru import logger

from jobscraper.models.job import (
    ContractType,
    DatePosted,
    ExperienceLevel,
    JobOffer,
    SearchCriteria,
    SortBy,
    WorkplaceType,
)
from jobscraper.scrapers.base import BaseScraper


class LinkedInScraper(BaseScraper):
    """Scraper pour les offres d'emploi LinkedIn."""

    name = "linkedin"
    base_url = "https://www.linkedin.com"

    APPLICATION_ACTIVITY_SELECTOR = (
        ".num-applicants__caption, "
        ".jobs-unified-top-card__applicant-count"
    )

    # Mapping des niveaux d'expérience LinkedIn
    EXPERIENCE_MAPPING = {
        ExperienceLevel.INTERNSHIP: "1",  # Internship
        ExperienceLevel.JUNIOR: "2",      # Entry level
        ExperienceLevel.MID: "3",         # Associate
        ExperienceLevel.SENIOR: "4",      # Mid-Senior level
        ExperienceLevel.LEAD: "5",        # Director
        ExperienceLevel.DIRECTOR: "6",    # Executive
    }

    # Mapping des types de contrat LinkedIn
    CONTRACT_MAPPING = {
        ContractType.CDI: "F",        # Full-time
        ContractType.CDD: "C",        # Contract
        ContractType.INTERIM: "T",    # Temporary
        ContractType.STAGE: "I",      # Internship
        ContractType.ALTERNANCE: "I", # Internship (closest match)
        ContractType.FREELANCE: "C",  # Contract
        ContractType.OTHER: "O",      # Other
    }

    # Mapping des types de lieu de travail
    WORKPLACE_MAPPING = {
        WorkplaceType.ON_SITE: "1",   # On-site
        WorkplaceType.REMOTE: "2",    # Remote
        WorkplaceType.HYBRID: "3",    # Hybrid
    }

    # Mapping des dates de publication
    DATE_POSTED_MAPPING = {
        DatePosted.PAST_24H: "r86400",     # Past 24 hours
        DatePosted.PAST_WEEK: "r604800",   # Past week
        DatePosted.PAST_MONTH: "r2592000", # Past month
        DatePosted.ANY_TIME: "",           # Any time (no filter)
    }

    # Mapping du tri
    SORT_MAPPING = {
        SortBy.RELEVANCE: "R",  # Most relevant
        SortBy.DATE: "DD",      # Most recent
    }

    # Rayons de recherche disponibles (en miles pour LinkedIn)
    RADIUS_MAPPING = {
        5: "5",
        10: "10",
        25: "25",
        50: "50",
        100: "100",
    }

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialise le scraper LinkedIn.

        Args:
            config: Configuration optionnelle
        """
        super().__init__(config)
        self.delay_between_requests = config.get("delay", 2) if config else 2

    def search(self, criteria: SearchCriteria) -> Iterator[JobOffer]:
        """
        Recherche des offres d'emploi sur LinkedIn.

        Args:
            criteria: Critères de recherche

        Yields:
            JobOffer: Les offres d'emploi trouvées
        """
        url = self._build_search_url(criteria)
        logger.info(f"Recherche LinkedIn: {url}")

        start = 0
        jobs_found = 0
        max_results = criteria.max_results
        seen_ids: set = set()  # Pour la déduplication

        while jobs_found < max_results:
            page_url = f"{url}&start={start}"
            logger.debug(f"Récupération page: {page_url}")

            try:
                html = self._fetch_page(page_url)
                soup = BeautifulSoup(html, "lxml")

                job_cards = self._extract_job_cards(soup)

                if not job_cards:
                    logger.info("Plus d'offres trouvées")
                    break

                new_jobs_on_page = 0
                for card in job_cards:
                    if jobs_found >= max_results:
                        break

                    job = self._parse_job_card(card)
                    if job:
                        # Déduplication par ID
                        if job.id in seen_ids:
                            logger.debug(f"Doublon ignoré: {job.id}")
                            continue
                        seen_ids.add(job.id)
                        jobs_found += 1
                        new_jobs_on_page += 1
                        yield job

                # Si aucune nouvelle offre sur cette page, on arrête
                if new_jobs_on_page == 0:
                    logger.info("Plus de nouvelles offres")
                    break

                start += 25  # LinkedIn pagine par 25
                time.sleep(self.delay_between_requests)

            except Exception as e:
                logger.error(f"Erreur lors de la recherche: {e}")
                break

        logger.info(f"Recherche terminée: {jobs_found} offres uniques trouvées")

    def get_job_details(self, job_id: str) -> Optional[JobOffer]:
        """
        Récupère les détails complets d'une offre LinkedIn.

        Args:
            job_id: Identifiant de l'offre LinkedIn

        Returns:
            JobOffer avec tous les détails ou None
        """
        url = f"{self.base_url}/jobs/view/{job_id}"
        logger.debug(f"Récupération détails: {url}")

        try:
            html = self._fetch_page(url)
            soup = BeautifulSoup(html, "lxml")
            return self._parse_job_details(soup, job_id, url)
        except Exception as e:
            logger.error(f"Erreur récupération détails job {job_id}: {e}")
            return None

    @staticmethod
    def is_over_100_application_activity(text: Optional[str]) -> bool:
        """Indique si le texte signale plus de 100 candidatures ou clics."""
        if not text:
            return False

        normalized = unicodedata.normalize("NFKD", text)
        normalized = "".join(
            character for character in normalized if not unicodedata.combining(character)
        )
        normalized = re.sub(r"\s+", " ", normalized).strip().lower()

        activity_markers = (
            "applicant",
            "candidat",
            "candidature",
            "people clicked apply",
            "personnes ont clique sur postuler",
            "personnes ont clique pour postuler",
        )
        if not any(marker in normalized for marker in activity_markers):
            return False

        match = re.search(r"(\d[\d\s,.]*)\s*\+?", normalized)
        if not match:
            return False

        number_text = re.sub(r"\D", "", match.group(1))
        if not number_text:
            return False

        count = int(number_text)
        if count > 100:
            return True

        threshold_marker = re.search(
            r"(?:over|more than|plus de)\s+100\b|\b100\s*\+",
            normalized,
        )
        return count == 100 and threshold_marker is not None

    def _build_search_url(self, criteria: SearchCriteria) -> str:
        """
        Construit l'URL de recherche LinkedIn avec tous les filtres disponibles.

        Args:
            criteria: Critères de recherche

        Returns:
            URL de recherche formatée
        """
        base = f"{self.base_url}/jobs/search"

        # Construire les mots-clés avec le titre si spécifié
        keywords_parts = []
        if criteria.title:
            keywords_parts.append(f'"{criteria.title}"')  # Recherche exacte
        if criteria.keywords:
            keywords_parts.extend(criteria.keywords)

        params = {
            "keywords": " ".join(keywords_parts),
            "location": criteria.location,
            "trk": "public_jobs_jobs-search-bar_search-submit",
            "position": "1",
            "pageNum": "0",
        }

        # Tri des résultats
        if criteria.sort_by:
            params["sortBy"] = self.SORT_MAPPING.get(criteria.sort_by, "R")

        # Filtre par niveau d'expérience
        if criteria.experience_levels:
            exp_codes = [
                self.EXPERIENCE_MAPPING.get(exp)
                for exp in criteria.experience_levels
                if exp in self.EXPERIENCE_MAPPING
            ]
            if exp_codes:
                params["f_E"] = ",".join(filter(None, exp_codes))

        # Filtre par type de contrat
        if criteria.contract_types:
            contract_codes = [
                self.CONTRACT_MAPPING.get(ct)
                for ct in criteria.contract_types
                if ct in self.CONTRACT_MAPPING
            ]
            if contract_codes:
                params["f_JT"] = ",".join(filter(None, contract_codes))

        # Filtre par type de lieu de travail (remote, hybrid, on-site)
        if criteria.workplace_types:
            workplace_codes = [
                self.WORKPLACE_MAPPING.get(wt)
                for wt in criteria.workplace_types
                if wt in self.WORKPLACE_MAPPING
            ]
            if workplace_codes:
                params["f_WT"] = ",".join(filter(None, workplace_codes))

        # Filtre par date de publication
        if criteria.date_posted and criteria.date_posted != DatePosted.ANY_TIME:
            date_code = self.DATE_POSTED_MAPPING.get(criteria.date_posted)
            if date_code:
                params["f_TPR"] = date_code
        elif criteria.posted_within_days:  # Compatibilité
            if criteria.posted_within_days <= 1:
                params["f_TPR"] = "r86400"
            elif criteria.posted_within_days <= 7:
                params["f_TPR"] = "r604800"
            elif criteria.posted_within_days <= 30:
                params["f_TPR"] = "r2592000"

        # Distance / Rayon de recherche
        if criteria.radius_km:
            # Trouver le rayon le plus proche disponible
            available_radii = sorted(self.RADIUS_MAPPING.keys())
            closest = min(available_radii, key=lambda x: abs(x - criteria.radius_km))
            params["distance"] = self.RADIUS_MAPPING[closest]

        # Filtre par entreprises spécifiques
        if criteria.companies:
            # LinkedIn utilise les IDs d'entreprise, mais on peut chercher par nom
            params["f_C"] = ",".join(criteria.companies)

        return f"{base}?{urlencode(params)}"

    def _extract_job_cards(self, soup: BeautifulSoup) -> list:
        """
        Extrait les cartes d'offres d'emploi de la page.

        Args:
            soup: BeautifulSoup de la page

        Returns:
            Liste des éléments de carte d'offre
        """
        # Sélecteurs pour les pages publiques LinkedIn
        selectors = [
            "div.base-card",
            "li.jobs-search-results__list-item",
            "div.job-search-card",
        ]

        for selector in selectors:
            cards = soup.select(selector)
            if cards:
                return cards

        return []

    def _is_masked(self, text: Optional[str]) -> bool:
        """
        Vérifie si un texte est masqué par LinkedIn (contient trop d'astérisques).

        Args:
            text: Texte à vérifier

        Returns:
            True si le texte est masqué
        """
        if not text:
            return True
        # Compter les astérisques
        asterisk_count = text.count("*")
        # Si plus de 30% du texte sont des astérisques, c'est masqué
        if len(text) > 0 and asterisk_count / len(text) > 0.3:
            return True
        return False

    def _parse_job_card(self, card) -> Optional[JobOffer]:
        """
        Parse une carte d'offre d'emploi.

        Args:
            card: Élément BeautifulSoup de la carte

        Returns:
            JobOffer ou None si parsing échoue ou offre masquée
        """
        try:
            # Titre
            title_elem = card.select_one(
                "h3.base-search-card__title, "
                "a.job-card-list__title, "
                "h3.job-card-title"
            )
            title = title_elem.get_text(strip=True) if title_elem else None

            # Entreprise
            company_elem = card.select_one(
                "h4.base-search-card__subtitle, "
                "a.job-card-container__company-name, "
                "h4.job-card-subtitle"
            )
            company = company_elem.get_text(strip=True) if company_elem else None

            # Filtrer les offres masquées par LinkedIn
            if self._is_masked(title) or self._is_masked(company):
                logger.debug(f"Offre masquée ignorée: {title} @ {company}")
                return None

            # Localisation
            location_elem = card.select_one(
                "span.job-search-card__location, "
                "li.job-card-container__metadata-item, "
                "span.job-card-location"
            )
            location = location_elem.get_text(strip=True) if location_elem else None

            # URL et ID
            link_elem = card.select_one("a.base-card__full-link, a.job-card-list__title")
            url = link_elem.get("href") if link_elem else None

            # Extraire l'ID de l'URL (format: /jobs/view/titre-slug-123456)
            job_id = None
            if url:
                # L'ID est le dernier nombre dans l'URL avant les paramètres
                match = re.search(r"-(\d+)(?:\?|$)", url)
                if not match:
                    # Fallback: chercher dans data-entity-urn
                    urn = card.get("data-entity-urn", "")
                    match = re.search(r"jobPosting:(\d+)", urn)
                if match:
                    job_id = match.group(1)

            if not all([title, company, job_id]):
                return None

            # Date de publication
            posted_at = self._parse_posted_date(card)

            return JobOffer(
                id=f"linkedin_{job_id}",
                source=self.name,
                url=url or f"{self.base_url}/jobs/view/{job_id}",
                title=title,
                company=company,
                location=location or "France",
                posted_at=posted_at,
            )

        except Exception as e:
            logger.debug(f"Erreur parsing carte: {e}")
            return None

    def _parse_posted_date(self, card) -> Optional[datetime]:
        """
        Parse la date de publication depuis la carte.

        Args:
            card: Élément BeautifulSoup

        Returns:
            datetime ou None
        """
        date_elem = card.select_one(
            "time.job-search-card__listdate, "
            "time.job-card-container__listed-date"
        )

        if date_elem:
            datetime_attr = date_elem.get("datetime")
            if datetime_attr:
                try:
                    return datetime.fromisoformat(datetime_attr.replace("Z", "+00:00"))
                except ValueError:
                    pass

            # Parse le texte relatif (ex: "il y a 2 jours")
            text = date_elem.get_text(strip=True).lower()
            return self._parse_relative_date(text)

        return None

    def _parse_relative_date(self, text: str) -> Optional[datetime]:
        """
        Parse une date relative en français ou anglais.

        Args:
            text: Texte de la date (ex: "il y a 2 jours")

        Returns:
            datetime ou None
        """
        now = datetime.now()

        patterns = [
            (r"(\d+)\s*(minute|min)", lambda m: now - timedelta(minutes=int(m.group(1)))),
            (r"(\d+)\s*(heure|hour|hr)", lambda m: now - timedelta(hours=int(m.group(1)))),
            (r"(\d+)\s*(jour|day)", lambda m: now - timedelta(days=int(m.group(1)))),
            (r"(\d+)\s*(semaine|week)", lambda m: now - timedelta(weeks=int(m.group(1)))),
            (r"(\d+)\s*(mois|month)", lambda m: now - timedelta(days=int(m.group(1)) * 30)),
        ]

        for pattern, handler in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return handler(match)

        return None

    def _parse_job_details(self, soup: BeautifulSoup, job_id: str, url: str) -> Optional[JobOffer]:
        """
        Parse les détails complets d'une offre.

        Args:
            soup: BeautifulSoup de la page de détails
            job_id: ID de l'offre
            url: URL de l'offre

        Returns:
            JobOffer avec détails complets
        """
        try:
            # Titre
            title = soup.select_one(
                "h1.top-card-layout__title, "
                "h1.jobs-unified-top-card__job-title"
            )
            title = title.get_text(strip=True) if title else "Unknown"

            # Entreprise
            company = soup.select_one(
                "a.topcard__org-name-link, "
                "span.jobs-unified-top-card__company-name"
            )
            company = company.get_text(strip=True) if company else "Unknown"

            # Localisation
            location = soup.select_one(
                "span.topcard__flavor--bullet, "
                "span.jobs-unified-top-card__bullet"
            )
            location = location.get_text(strip=True) if location else "France"

            # Description
            description = soup.select_one(
                "div.description__text, "
                "div.jobs-description__content"
            )
            description_text = description.get_text(strip=True) if description else None

            # Nombre de candidatures ou de clics sur le bouton Postuler
            application_activity_elem = soup.select_one(
                self.APPLICATION_ACTIVITY_SELECTOR
            )
            application_activity = (
                application_activity_elem.get_text(" ", strip=True)
                if application_activity_elem
                else None
            )

            # Critères (type de contrat, niveau d'expérience, etc.)
            criteria_items = soup.select(
                "li.description__job-criteria-item, "
                "li.jobs-unified-top-card__job-insight"
            )

            contract_type = None
            experience_level = None

            for item in criteria_items:
                text = item.get_text(strip=True).lower()
                if "cdi" in text or "full-time" in text or "temps plein" in text:
                    contract_type = ContractType.CDI
                elif "cdd" in text or "contract" in text:
                    contract_type = ContractType.CDD
                elif "stage" in text or "internship" in text:
                    contract_type = ContractType.STAGE
                elif "alternance" in text or "apprenticeship" in text:
                    contract_type = ContractType.ALTERNANCE

                if "entry" in text or "junior" in text or "débutant" in text:
                    experience_level = ExperienceLevel.JUNIOR
                elif "senior" in text or "confirmé" in text:
                    experience_level = ExperienceLevel.SENIOR
                elif "mid" in text or "intermédiaire" in text:
                    experience_level = ExperienceLevel.MID

            # Compétences
            skills = []
            skills_section = soup.select("span.job-details-skill-match-status-list__skill-name")
            for skill in skills_section:
                skills.append(skill.get_text(strip=True))

            return JobOffer(
                id=f"linkedin_{job_id}",
                source=self.name,
                url=url,
                title=title,
                company=company,
                location=location,
                description=description_text,
                contract_type=contract_type,
                experience_level=experience_level,
                skills=skills,
                application_activity=application_activity,
            )

        except Exception as e:
            logger.error(f"Erreur parsing détails: {e}")
            return None
