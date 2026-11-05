"""Scraper pour Welcome to the Jungle (WTTJ)."""

from datetime import datetime
from typing import Dict, Iterator, Optional, Tuple

import requests
from loguru import logger

from jobscraper.models.job import (
    ContractType,
    ExperienceLevel,
    JobOffer,
    SearchCriteria,
    WorkplaceType,
)
from jobscraper.scrapers.base import BaseScraper
from jobscraper.utils.geocoding import geocode


class WTTJScraper(BaseScraper):
    """
    Scraper pour Welcome to the Jungle via l'API Algolia.

    WTTJ utilise Algolia pour la recherche. Les clés API sont publiques
    et exposées dans le frontend du site.
    """

    name = "wttj"
    base_url = "https://www.welcometothejungle.com"

    # Configuration Algolia (clés publiques du site)
    ALGOLIA_APP_ID = "CSEKHVMS53"
    ALGOLIA_API_KEY = "4bd8f6215d0cc52b26430765769e65a0"
    ALGOLIA_INDEX = "wttj_jobs_production_fr"

    # Mapping des types de contrat WTTJ
    CONTRACT_MAPPING = {
        "full_time": ContractType.CDI,
        "part_time": ContractType.CDI,
        "fixed_term": ContractType.CDD,
        "temporary": ContractType.INTERIM,
        "internship": ContractType.STAGE,
        "apprenticeship": ContractType.ALTERNANCE,
        "freelance": ContractType.FREELANCE,
        "vie": ContractType.CDD,
    }

    # Mapping inverse pour les filtres
    CONTRACT_FILTER_MAPPING = {
        ContractType.CDI: ["full_time", "part_time"],
        ContractType.CDD: ["fixed_term", "vie"],
        ContractType.INTERIM: ["temporary"],
        ContractType.STAGE: ["internship"],
        ContractType.ALTERNANCE: ["apprenticeship"],
        ContractType.FREELANCE: ["freelance"],
    }

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialise le scraper WTTJ.

        Args:
            config: Configuration optionnelle
        """
        super().__init__(config)
        self.hits_per_page = 20

    def search(self, criteria: SearchCriteria) -> Iterator[JobOffer]:
        """
        Recherche des offres d'emploi sur WTTJ via Algolia.

        Args:
            criteria: Critères de recherche

        Yields:
            JobOffer: Les offres d'emploi trouvées
        """
        jobs_found = 0
        max_results = criteria.max_results
        page = 0
        seen_ids: set = set()

        # Géocodage pour recherche avec rayon
        coords: Optional[Tuple[float, float]] = None
        radius_m: Optional[int] = None
        use_geo = False
        if criteria.location and criteria.radius_km:
            coords = geocode(criteria.location)
            if coords:
                radius_m = criteria.radius_km * 1000  # Convertir km en mètres
                use_geo = True

        query = self._build_query(criteria, use_geo=use_geo)
        filters = self._build_filters(criteria)

        if use_geo:
            logger.info(f"Recherche WTTJ: query='{query}', coords={coords}, rayon={criteria.radius_km}km")
        elif criteria.location and criteria.radius_km:
            logger.warning(f"Géocodage échoué pour {criteria.location}, recherche sans rayon")
            logger.info(f"Recherche WTTJ: query='{query}'")
        else:
            logger.info(f"Recherche WTTJ: query='{query}'")

        while jobs_found < max_results:
            try:
                results = self._fetch_algolia(query, filters, page, coords, radius_m)

                if not results.get("hits"):
                    logger.info("Plus d'offres trouvées")
                    break

                for hit in results["hits"]:
                    if jobs_found >= max_results:
                        break

                    job = self._parse_hit(hit)
                    if job:
                        if job.id in seen_ids:
                            continue
                        seen_ids.add(job.id)
                        jobs_found += 1
                        yield job

                # Vérifier s'il y a plus de pages
                total_pages = results.get("nbPages", 1)
                if page >= total_pages - 1:
                    break

                page += 1

            except Exception as e:
                logger.error(f"Erreur recherche WTTJ: {e}")
                break

        logger.info(f"Recherche terminée: {jobs_found} offres uniques trouvées")

    def get_job_details(self, job_id: str) -> Optional[JobOffer]:
        """
        WTTJ retourne déjà tous les détails via Algolia.
        """
        return None

    def _fetch_algolia(
        self,
        query: str,
        filters: str,
        page: int,
        coords: Optional[Tuple[float, float]] = None,
        radius_m: Optional[int] = None,
    ) -> dict:
        """
        Effectue une requête vers l'API Algolia.

        Args:
            query: Texte de recherche
            filters: Filtres Algolia
            page: Numéro de page
            coords: Coordonnées GPS (lat, lng) pour recherche géographique
            radius_m: Rayon de recherche en mètres

        Returns:
            Réponse JSON d'Algolia
        """
        url = f"https://{self.ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/{self.ALGOLIA_INDEX}/query"

        headers = {
            "X-Algolia-API-Key": self.ALGOLIA_API_KEY,
            "X-Algolia-Application-Id": self.ALGOLIA_APP_ID,
            "Content-Type": "application/json",
            "Referer": "https://www.welcometothejungle.com/",
            "Origin": "https://www.welcometothejungle.com",
        }

        payload = {
            "query": query,
            "hitsPerPage": self.hits_per_page,
            "page": page,
        }

        if filters:
            payload["filters"] = filters

        # Ajouter la recherche géographique si coordonnées fournies
        if coords and radius_m:
            payload["aroundLatLng"] = f"{coords[0]},{coords[1]}"
            payload["aroundRadius"] = radius_m

        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def _build_query(self, criteria: SearchCriteria, use_geo: bool = False) -> str:
        """
        Construit la requête de recherche.

        Args:
            criteria: Critères de recherche
            use_geo: Si True, n'inclut pas la location (filtrée par géoloc)

        Returns:
            Texte de recherche
        """
        parts = []

        if criteria.title:
            parts.append(criteria.title)
        if criteria.keywords:
            parts.extend(criteria.keywords)
        # N'ajouter la location que si on n'utilise pas la recherche géographique
        if not use_geo and criteria.location and criteria.location.lower() != "france":
            parts.append(criteria.location)

        return " ".join(parts) if parts else ""

    def _build_filters(self, criteria: SearchCriteria) -> str:
        """
        Construit les filtres Algolia.

        Args:
            criteria: Critères de recherche

        Returns:
            Chaîne de filtres Algolia
        """
        filters = []

        # Type de contrat
        if criteria.contract_types:
            contract_filters = []
            for ct in criteria.contract_types:
                if ct in self.CONTRACT_FILTER_MAPPING:
                    for wttj_type in self.CONTRACT_FILTER_MAPPING[ct]:
                        contract_filters.append(f"contract_type:{wttj_type}")
            if contract_filters:
                filters.append(f"({' OR '.join(contract_filters)})")

        # Remote
        if criteria.workplace_types:
            if WorkplaceType.REMOTE in criteria.workplace_types:
                filters.append("(remote:fulltime OR remote:partial)")
            elif WorkplaceType.ON_SITE in criteria.workplace_types:
                filters.append("remote:no")

        return " AND ".join(filters) if filters else ""

    def _parse_hit(self, hit: dict) -> Optional[JobOffer]:
        """
        Parse un résultat Algolia en JobOffer.

        Args:
            hit: Résultat Algolia

        Returns:
            JobOffer ou None
        """
        try:
            job_id = hit.get("reference") or hit.get("objectID")
            if not job_id:
                return None

            title = hit.get("name", "").strip()
            if not title:
                return None

            # Entreprise
            organization = hit.get("organization", {})
            company = organization.get("name", "").strip()

            # Localisation
            offices = hit.get("offices", [])
            if offices:
                office = offices[0]
                city = office.get("city", "")
                country = office.get("country", "")
                location = f"{city}, {country}" if city else country
            else:
                location = "France"

            # URL
            org_slug = organization.get("slug", "")
            job_slug = hit.get("slug", "")
            url = f"{self.base_url}/fr/companies/{org_slug}/jobs/{job_slug}" if org_slug and job_slug else ""

            # Description
            description = hit.get("summary") or ""

            # Type de contrat
            contract_wttj = hit.get("contract_type", "")
            contract_type = self.CONTRACT_MAPPING.get(contract_wttj)

            # Date de publication
            posted_at = None
            published = hit.get("published_at")
            if published:
                try:
                    posted_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except ValueError:
                    pass

            # Niveau d'expérience
            exp_min = hit.get("experience_level_minimum")
            experience_level = None
            if exp_min is not None:
                if exp_min <= 1:
                    experience_level = ExperienceLevel.JUNIOR
                elif exp_min <= 3:
                    experience_level = ExperienceLevel.MID
                elif exp_min <= 6:
                    experience_level = ExperienceLevel.SENIOR
                else:
                    experience_level = ExperienceLevel.LEAD

            return JobOffer(
                id=f"wttj_{job_id}",
                source=self.name,
                url=url,
                title=title,
                company=company or "Non spécifié",
                location=location or "France",
                description=description,
                contract_type=contract_type,
                experience_level=experience_level,
                posted_at=posted_at,
            )

        except Exception as e:
            logger.debug(f"Erreur parsing hit WTTJ: {e}")
            return None
