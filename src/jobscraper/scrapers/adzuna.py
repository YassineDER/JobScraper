"""Scraper pour Adzuna API (agrégateur: Indeed, Monster, etc.)."""

import os
from datetime import datetime
from typing import Dict, Iterator, List, Optional

import requests
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


class AdzunaScraper(BaseScraper):
    """
    Scraper pour Adzuna API.

    Adzuna agrège les offres de: Indeed, Monster, CareerBuilder, etc.
    API gratuite avec inscription: https://developer.adzuna.com/
    """

    name = "adzuna"
    base_url = "https://api.adzuna.com/v1/api/jobs"

    # Mapping des types de contrat
    CONTRACT_MAPPING = {
        ContractType.CDI: "permanent",
        ContractType.CDD: "contract",
        ContractType.STAGE: "graduate",
        ContractType.ALTERNANCE: "graduate",
        ContractType.INTERIM: "contract",
        ContractType.FREELANCE: "contract",
    }

    # Mapping du tri
    SORT_MAPPING = {
        SortBy.RELEVANCE: "relevance",
        SortBy.DATE: "date",
    }

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialise le scraper Adzuna.

        Args:
            config: Configuration avec app_id et app_key
        """
        super().__init__(config)
        self.app_id = config.get("app_id") if config else os.getenv("ADZUNA_APP_ID")
        self.app_key = config.get("app_key") if config else os.getenv("ADZUNA_APP_KEY")
        self.country = config.get("country", "fr") if config else "fr"

        if not self.app_id or not self.app_key:
            logger.warning(
                "Clés API Adzuna non configurées. "
                "Définir ADZUNA_APP_ID et ADZUNA_APP_KEY dans .env "
                "ou obtenir des clés sur https://developer.adzuna.com/"
            )

    def search(self, criteria: SearchCriteria) -> Iterator[JobOffer]:
        """
        Recherche des offres d'emploi via l'API Adzuna.

        Args:
            criteria: Critères de recherche

        Yields:
            JobOffer: Les offres d'emploi trouvées
        """
        if not self.app_id or not self.app_key:
            logger.error("Clés API Adzuna manquantes")
            return

        page = 1
        jobs_found = 0
        max_results = criteria.max_results
        seen_ids: set = set()
        results_per_page = 50  # Max autorisé par Adzuna

        while jobs_found < max_results:
            url = self._build_search_url(criteria, page, results_per_page)
            logger.debug(f"Requête Adzuna: {url}")

            try:
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])
                if not results:
                    logger.info("Plus d'offres trouvées")
                    break

                new_jobs_on_page = 0
                for result in results:
                    if jobs_found >= max_results:
                        break

                    job = self._parse_result(result)
                    if job:
                        if job.id in seen_ids:
                            continue
                        seen_ids.add(job.id)
                        jobs_found += 1
                        new_jobs_on_page += 1
                        yield job

                if new_jobs_on_page == 0:
                    logger.info("Plus de nouvelles offres")
                    break

                # Vérifier s'il y a plus de pages
                total_count = data.get("count", 0)
                if page * results_per_page >= total_count:
                    break

                page += 1

            except requests.RequestException as e:
                logger.error(f"Erreur API Adzuna: {e}")
                break
            except Exception as e:
                logger.error(f"Erreur lors de la recherche: {e}")
                break

        logger.info(f"Recherche terminée: {jobs_found} offres uniques trouvées")

    def get_job_details(self, job_id: str) -> Optional[JobOffer]:
        """
        Adzuna ne fournit pas de détails supplémentaires via l'API.
        Les détails sont déjà inclus dans les résultats de recherche.
        """
        return None

    def _build_search_url(self, criteria: SearchCriteria, page: int, results_per_page: int) -> str:
        """
        Construit l'URL de requête API Adzuna.

        Args:
            criteria: Critères de recherche
            page: Numéro de page
            results_per_page: Nombre de résultats par page

        Returns:
            URL de l'API formatée
        """
        base = f"{self.base_url}/{self.country}/search/{page}"

        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": min(results_per_page, 50),
            "content-type": "application/json",
        }

        # Mots-clés
        keywords_parts = []
        if criteria.title:
            keywords_parts.append(criteria.title)
        if criteria.keywords:
            keywords_parts.extend(criteria.keywords)
        if keywords_parts:
            params["what"] = " ".join(keywords_parts)

        # Localisation
        if criteria.location and criteria.location != "France":
            params["where"] = criteria.location

        # Rayon de recherche (en km)
        if criteria.radius_km:
            params["distance"] = criteria.radius_km

        # Type de contrat
        if criteria.contract_types:
            contract_type = criteria.contract_types[0]
            if contract_type in self.CONTRACT_MAPPING:
                params["contract_type"] = self.CONTRACT_MAPPING[contract_type]

        # Temps plein/partiel
        if criteria.contract_types:
            if ContractType.CDI in criteria.contract_types:
                params["full_time"] = 1

        # Date de publication
        if criteria.date_posted:
            if criteria.date_posted == DatePosted.PAST_24H:
                params["max_days_old"] = 1
            elif criteria.date_posted == DatePosted.PAST_WEEK:
                params["max_days_old"] = 7
            elif criteria.date_posted == DatePosted.PAST_MONTH:
                params["max_days_old"] = 30

        # Tri
        if criteria.sort_by:
            params["sort_by"] = self.SORT_MAPPING.get(criteria.sort_by, "relevance")

        # Salaire minimum
        if criteria.salary_min:
            params["salary_min"] = int(criteria.salary_min)

        # Construire l'URL
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{base}?{query_string}"

    def _parse_result(self, result: dict) -> Optional[JobOffer]:
        """
        Parse un résultat de l'API Adzuna.

        Args:
            result: Dictionnaire de résultat de l'API

        Returns:
            JobOffer ou None
        """
        try:
            job_id = result.get("id")
            if not job_id:
                return None

            title = result.get("title", "").strip()
            company = result.get("company", {}).get("display_name", "").strip()
            location = result.get("location", {}).get("display_name", "").strip()

            if not title or not company:
                return None

            # URL de l'offre
            url = result.get("redirect_url", "")

            # Description
            description = result.get("description", "")

            # Date de publication
            created = result.get("created")
            posted_at = None
            if created:
                try:
                    posted_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except ValueError:
                    pass

            # Type de contrat
            contract_type = None
            contract_data = result.get("contract_type")
            if contract_data:
                if contract_data == "permanent":
                    contract_type = ContractType.CDI
                elif contract_data == "contract":
                    contract_type = ContractType.CDD

            # Catégorie
            category = result.get("category", {}).get("label", "")

            return JobOffer(
                id=f"adzuna_{job_id}",
                source=self.name,
                url=url,
                title=title,
                company=company,
                location=location or "France",
                description=description,
                contract_type=contract_type,
                posted_at=posted_at,
            )

        except Exception as e:
            logger.debug(f"Erreur parsing résultat Adzuna: {e}")
            return None
