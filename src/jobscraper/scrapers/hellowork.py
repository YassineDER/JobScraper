"""Scraper pour HelloWork."""

import json
import re
import time
from datetime import datetime, timedelta
from typing import Dict, Iterator, Optional
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup
from loguru import logger

from jobscraper.models.job import (
    ContractType,
    DatePosted,
    ExperienceLevel,
    JobOffer,
    SearchCriteria,
    SortBy,
)
from jobscraper.scrapers.base import BaseScraper
from jobscraper.utils.experience import experience_level_from_description
from jobscraper.utils.title_filters import junior_title_is_excluded


class HelloWorkScraper(BaseScraper):
    """Scraper pour les offres d'emploi HelloWork."""

    name = "hellowork"
    base_url = "https://www.hellowork.com"

    # Mapping des types de contrat HelloWork
    CONTRACT_MAPPING = {
        ContractType.CDI: "CDI",
        ContractType.CDD: "CDD",
        ContractType.INTERIM: "interim",
        ContractType.STAGE: "stage",
        ContractType.ALTERNANCE: "alternance",
        ContractType.FREELANCE: "freelance",
    }

    # Mapping des dates de publication
    DATE_POSTED_MAPPING = {
        DatePosted.PAST_24H: "1",
        DatePosted.PAST_WEEK: "7",
        DatePosted.PAST_MONTH: "30",
        DatePosted.ANY_TIME: "",
    }

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialise le scraper HelloWork.

        Args:
            config: Configuration optionnelle
        """
        super().__init__(config)
        self.delay_between_requests = config.get("delay", 2) if config else 2
        self._last_request_at: Optional[float] = None
        self._known_jobs: Dict[str, JobOffer] = {}
        self._details_cache: Dict[str, Optional[JobOffer]] = {}
        self.detail_failures = 0

    def _get_headers(self) -> dict:
        """
        Retourne les headers HTTP pour HelloWork.
        Note: On évite brotli (br) car requests ne le supporte pas nativement.
        """
        return {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

    def search(self, criteria: SearchCriteria) -> Iterator[JobOffer]:
        """
        Recherche des offres d'emploi sur HelloWork.

        Args:
            criteria: Critères de recherche

        Yields:
            JobOffer: Les offres d'emploi trouvées
        """
        url = self._build_search_url(criteria)
        logger.info(f"Recherche HelloWork: {url}")

        page = 1
        jobs_found = 0
        max_results = criteria.max_results
        seen_ids: set = set()
        filter_junior = ExperienceLevel.JUNIOR in criteria.experience_levels
        junior_title_exclusions = 0
        unknown_experience_count = 0
        self._known_jobs.clear()
        self._details_cache.clear()
        self.detail_failures = 0

        while jobs_found < max_results:
            page_url = f"{url}&p={page}" if page > 1 else url
            logger.debug(f"Récupération page {page}: {page_url}")

            try:
                self._wait_before_request()
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
                        if job.id in seen_ids:
                            logger.debug(f"Doublon ignoré: {job.id}")
                            continue
                        seen_ids.add(job.id)
                        # Count unseen candidates, not accepted results: a page
                        # entirely excluded by the junior filter is not the end.
                        new_jobs_on_page += 1
                        if filter_junior and junior_title_is_excluded(job.title):
                            junior_title_exclusions += 1
                            continue
                        self._known_jobs[job.id] = job
                        self._known_jobs[str(job.url)] = job
                        detailed = self.get_job_details(job.id)
                        if detailed is None:
                            continue
                        if (
                            criteria.experience_levels
                            and detailed.experience_level is None
                        ):
                            unknown_experience_count += 1
                        if (
                            criteria.experience_levels
                            and detailed.experience_level not in criteria.experience_levels
                        ):
                            continue
                        if (
                            criteria.contract_types
                            and detailed.contract_type not in criteria.contract_types
                        ):
                            continue
                        jobs_found += 1
                        yield detailed

                if new_jobs_on_page == 0:
                    logger.info("Plus de nouvelles offres")
                    break

                if jobs_found >= max_results:
                    break

                page += 1

            except Exception as e:
                logger.error(f"Erreur lors de la recherche: {e}")
                break

        logger.info(f"Recherche terminée: {jobs_found} offres uniques trouvées")
        if junior_title_exclusions:
            logger.info(
                "HelloWork: {} offre(s) exclue(s) "
                "(titre incompatible avec le filtre junior)",
                junior_title_exclusions,
            )
        if unknown_experience_count:
            logger.info(
                "HelloWork: {} offre(s) exclue(s), expérience non identifiable "
                "dans les détails",
                unknown_experience_count,
            )
        if self.detail_failures:
            logger.warning(
                "HelloWork: {} offre(s) exclue(s), détails inaccessibles ou incomplets. "
                "Les résultats peuvent être incomplets.",
                self.detail_failures,
            )

    def get_job_details(self, job_id: str) -> Optional[JobOffer]:
        """
        Récupère les détails complets d'une offre HelloWork.

        Args:
            job_id: Identifiant de l'offre HelloWork

        Returns:
            JobOffer avec tous les détails ou None
        """
        listing = self._known_jobs.get(job_id)
        if listing:
            url = str(listing.url)
        elif job_id.startswith("https://"):
            url = job_id
        else:
            numeric_id = job_id.removeprefix("hellowork_")
            url = f"{self.base_url}/fr-fr/emplois/{numeric_id}.html"

        parts = urlsplit(url)
        match = re.fullmatch(r"/fr-fr/emplois/(\d+)\.html", parts.path)
        if (
            parts.scheme != "https"
            or parts.netloc not in ("www.hellowork.com", "hellowork.com")
            or match is None
        ):
            logger.warning("Identifiant ou URL HelloWork invalide: {}", job_id)
            return None

        numeric_id = match.group(1)
        if numeric_id in self._details_cache:
            cached = self._details_cache[numeric_id]
            return cached.model_copy(deep=True) if cached else None

        logger.debug("Récupération détails: {}", url)
        detailed = None
        try:
            self._wait_before_request()
            html = self._fetch_page(url)
            soup = BeautifulSoup(html, "lxml")
            detailed = self._parse_job_details(soup, numeric_id, url, listing)
            if detailed is None:
                raise ValueError("Fiche JobPosting ou description absente")
        except Exception as exc:
            self.detail_failures += 1
            logger.warning("Détails HelloWork non vérifiés pour {}: {}", url, exc)

        self._details_cache[numeric_id] = detailed
        return detailed.model_copy(deep=True) if detailed else None

    def _wait_before_request(self) -> None:
        """Espace les requêtes de liste et de détail vers HelloWork."""
        if self._last_request_at is not None:
            remaining = self.delay_between_requests - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _build_search_url(self, criteria: SearchCriteria) -> str:
        """
        Construit l'URL de recherche HelloWork.

        Args:
            criteria: Critères de recherche

        Returns:
            URL de recherche formatée
        """
        base = f"{self.base_url}/fr-fr/emploi/recherche.html"

        # Construire les mots-clés
        keywords_parts = []
        if criteria.title:
            keywords_parts.append(criteria.title)
        if criteria.keywords:
            keywords_parts.extend(criteria.keywords)

        params = {}

        if keywords_parts:
            params["k"] = " ".join(keywords_parts)

        if criteria.location:
            params["l"] = criteria.location

        # Type de contrat
        if criteria.contract_types:
            contract_codes = [
                self.CONTRACT_MAPPING.get(ct)
                for ct in criteria.contract_types
                if ct in self.CONTRACT_MAPPING
            ]
            if contract_codes:
                # HelloWork expects repeated c parameters for multiple
                # contracts; a comma-separated value makes the site return
                # zero offers (e.g. c=CDI%2CCDD).
                params["c"] = list(filter(None, contract_codes))

        # Date de publication
        if criteria.date_posted and criteria.date_posted != DatePosted.ANY_TIME:
            date_code = self.DATE_POSTED_MAPPING.get(criteria.date_posted)
            if date_code:
                params["d"] = date_code

        # Rayon de recherche (en km)
        if criteria.radius_km:
            params["ray"] = str(criteria.radius_km)

        return f"{base}?{urlencode(params, doseq=True)}" if params else base

    def _extract_job_cards(self, soup: BeautifulSoup) -> list:
        """
        Extrait les cartes d'offres d'emploi de la page.

        Args:
            soup: BeautifulSoup de la page

        Returns:
            Liste des éléments de carte d'offre
        """
        # Sélecteur principal pour HelloWork (structure 2024-2025)
        cards = soup.select("li[data-id-storage-item-id]")
        if cards:
            logger.debug(f"{len(cards)} offres trouvées")
            return cards

        # Fallback: chercher via les liens d'offres
        offer_links = soup.select("a[href*='/fr-fr/emplois/']")
        if offer_links:
            cards = []
            seen_hrefs = set()
            for link in offer_links:
                href = link.get("href", "")
                if href in seen_hrefs:
                    continue
                seen_hrefs.add(href)
                parent = link.find_parent("li")
                if parent and parent not in cards:
                    cards.append(parent)
            if cards:
                logger.debug(f"Fallback: {len(cards)} offres trouvées via liens")
                return cards

        return []

    def _parse_job_card(self, card) -> Optional[JobOffer]:
        """
        Parse une carte d'offre d'emploi HelloWork.

        Args:
            card: Élément BeautifulSoup de la carte (li[data-id-storage-item-id])

        Returns:
            JobOffer ou None si parsing échoue
        """
        try:
            # ID depuis l'attribut data
            job_id = card.get("data-id-storage-item-id")
            if not job_id:
                return None

            # URL
            link = card.select_one("a[href*='/emplois/']")
            url = link.get("href", "") if link else f"/fr-fr/emplois/{job_id}.html"
            if url.startswith("/"):
                url = f"{self.base_url}{url}"

            # Titre depuis l'input hidden (plus fiable)
            title_input = card.select_one('input[name="title"]')
            if title_input:
                title = title_input.get("value", "").strip()
            else:
                # Fallback: depuis le paragraphe
                title_elem = card.select_one("p.tw-typo-l, h3, h2")
                title = title_elem.get_text(strip=True) if title_elem else None

            # Entreprise depuis l'input hidden
            company_input = card.select_one('input[name="company"]')
            if company_input:
                company = company_input.get("value", "").strip()
            else:
                # Fallback
                company_elem = card.select_one("p.tw-typo-s.tw-inline")
                company = company_elem.get_text(strip=True) if company_elem else None

            # Tags (localisation, contrat, salaire, télétravail)
            tags = card.select("div.tw-tag-secondary-s, div.tw-readonly.tw-tag-secondary-s")

            location = None
            contract_type = None
            salary_text = None

            for tag in tags:
                text = tag.get_text(strip=True)

                # Localisation (contient un code postal ou arrondissement)
                if re.search(r"\d{2,5}$|^\d{5}", text) and not location:
                    location = text
                # Type de contrat
                elif text.upper() in ["CDI", "CDD", "STAGE", "ALTERNANCE", "INTÉRIM", "INTERIM", "FREELANCE"]:
                    contract_type = self._map_contract_type(text)
                # Salaire
                elif "€" in text:
                    salary_text = text

            # Date de publication
            date_elem = card.select_one("div.tw-text-grey-500, span.tw-text-grey-500")
            posted_at = None
            if date_elem:
                date_text = date_elem.get_text(strip=True)
                posted_at = self._parse_relative_date(date_text)

            # Validation minimale
            if not title:
                return None

            return JobOffer(
                id=f"hellowork_{job_id}",
                source=self.name,
                url=url,
                title=title,
                company=company or "Non spécifié",
                location=location or "France",
                contract_type=contract_type,
                posted_at=posted_at,
            )

        except Exception as e:
            logger.debug(f"Erreur parsing carte: {e}")
            return None

    def _map_contract_type(self, text: str) -> Optional[ContractType]:
        """
        Convertit le texte du type de contrat en ContractType.
        """
        text_upper = text.upper().strip()
        mapping = {
            "CDI": ContractType.CDI,
            "CDD": ContractType.CDD,
            "STAGE": ContractType.STAGE,
            "ALTERNANCE": ContractType.ALTERNANCE,
            "INTÉRIM": ContractType.INTERIM,
            "INTERIM": ContractType.INTERIM,
            "FREELANCE": ContractType.FREELANCE,
        }
        return mapping.get(text_upper)

    def _parse_posted_date(self, card) -> Optional[datetime]:
        """
        Parse la date de publication depuis la carte.

        Args:
            card: Élément BeautifulSoup

        Returns:
            datetime ou None
        """
        # Chercher un élément time
        time_elem = card.select_one("time")
        if time_elem:
            datetime_attr = time_elem.get("datetime")
            if datetime_attr:
                try:
                    return datetime.fromisoformat(datetime_attr.replace("Z", "+00:00"))
                except ValueError:
                    pass

        # Parser le texte relatif
        text = card.get_text().lower()
        return self._parse_relative_date(text)

    def _parse_relative_date(self, text: str) -> Optional[datetime]:
        """
        Parse une date relative en français.

        Args:
            text: Texte contenant la date

        Returns:
            datetime ou None
        """
        now = datetime.now()

        patterns = [
            (r"il y a (\d+)\s*minute", lambda m: now - timedelta(minutes=int(m.group(1)))),
            (r"il y a (\d+)\s*heure", lambda m: now - timedelta(hours=int(m.group(1)))),
            (r"il y a (\d+)\s*jour", lambda m: now - timedelta(days=int(m.group(1)))),
            (r"il y a (\d+)\s*semaine", lambda m: now - timedelta(weeks=int(m.group(1)))),
            (r"il y a (\d+)\s*mois", lambda m: now - timedelta(days=int(m.group(1)) * 30)),
            (r"aujourd'hui", lambda m: now),
            (r"hier", lambda m: now - timedelta(days=1)),
        ]

        for pattern, handler in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return handler(match)

        return None

    @staticmethod
    def _html_text(value: Optional[str]) -> str:
        """Convertit une description HTML en texte avec paragraphes lisibles."""
        if not isinstance(value, str):
            return ""
        fragment = BeautifulSoup(value, "lxml")
        for element in fragment.select("script, style"):
            element.decompose()
        for element in fragment.select("p, div, li, h1, h2, h3, h4, br"):
            element.insert_before("\n")
            element.insert_after("\n")
        return "\n".join(
            " ".join(line.split())
            for line in fragment.get_text(" ", strip=False).splitlines()
            if line.strip()
        )

    @staticmethod
    def _profile_requirements(description: str, qualifications: str) -> str:
        """Isole le profil candidat pour éviter les années d'âge de l'entreprise."""
        if qualifications:
            return qualifications
        sections = re.split(
            r"(?im)^\s*(?:le profil recherché|profil recherché|votre profil|"
            r"profil du candidat|qualifications)\s*$",
            description,
        )
        if len(sections) < 2:
            return description
        return re.split(
            r"(?im)^\s*(?:bienvenue chez|à propos de|qui sommes-nous|"
            r"pourquoi nous rejoindre|les avantages|processus de recrutement)\b.*$",
            sections[-1],
        )[0]

    def _parse_job_details(
        self,
        soup: BeautifulSoup,
        job_id: str,
        url: str,
        listing: Optional[JobOffer] = None,
    ) -> Optional[JobOffer]:
        """Lit la fiche structurée JobPosting, avec les métadonnées de liste en repli."""
        data = None
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                candidate = json.loads(script.string or script.get_text())
            except (TypeError, ValueError):
                continue
            if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                data = candidate
                break
        if data is None:
            return None

        description = self._html_text(data.get("description"))
        if not description:
            return None
        qualifications = self._html_text(data.get("qualifications"))
        if qualifications and qualifications not in description:
            full_description = f"{description}\n\n{qualifications}"
        else:
            full_description = description

        inferred = experience_level_from_description(
            self._profile_requirements(description, qualifications)
        )
        if inferred is None:
            requirement = data.get("experienceRequirements")
            months = requirement.get("monthsOfExperience") if isinstance(requirement, dict) else None
            if isinstance(months, (int, float, str)) and not isinstance(months, bool):
                try:
                    years = float(months) / 12
                    if years >= 0:
                        inferred = (
                            ExperienceLevel.JUNIOR if years < 3
                            else ExperienceLevel.SENIOR
                        )
                except ValueError:
                    pass

        organization = data.get("hiringOrganization")
        company = organization.get("name") if isinstance(organization, dict) else None
        place = data.get("jobLocation")
        if isinstance(place, list):
            place = next((item for item in place if isinstance(item, dict)), None)
        address = place.get("address") if isinstance(place, dict) else None
        city = address.get("addressLocality") if isinstance(address, dict) else None
        country = address.get("addressCountry") if isinstance(address, dict) else None
        location = f"{city}, France" if city and country == "FR" else city

        employment = data.get("employmentType") or []
        employment = [employment] if isinstance(employment, str) else employment
        contract_type = listing.contract_type if listing else None
        if isinstance(employment, list):
            if "TEMPORARY" in employment:
                contract_type = ContractType.CDD
            elif "INTERN" in employment:
                contract_type = ContractType.STAGE
            elif "CONTRACTOR" in employment:
                contract_type = ContractType.FREELANCE
            elif "FULL_TIME" in employment:
                contract_type = ContractType.CDI

        posted_at = listing.posted_at if listing else None
        if isinstance(data.get("datePosted"), str):
            try:
                posted_at = datetime.fromisoformat(data["datePosted"].replace("Z", "+00:00"))
            except ValueError:
                pass

        return JobOffer(
            id=f"hellowork_{job_id}",
            source=self.name,
            url=url,
            title=data.get("title") or (listing.title if listing else "Non spécifié"),
            company=company or (listing.company if listing else "Non spécifié"),
            location=location or (listing.location if listing else "France"),
            description=full_description,
            contract_type=contract_type,
            experience_level=inferred,
            posted_at=posted_at,
        )
