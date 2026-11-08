"""Scraper pour Welcome to the Jungle (WTTJ)."""

import re
from datetime import datetime, timedelta, timezone
from time import monotonic, sleep
from typing import Dict, Iterator, Optional, Tuple
from urllib.parse import quote, urlsplit

import requests
from bs4 import BeautifulSoup
from loguru import logger
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
    ALGOLIA_DATE_INDEX = "wttj_jobs_production_fr_published_at_desc"
    DETAILS_BASE_URL = "https://api.welcometothejungle.com/api/v1/organizations"

    # Mapping des types de contrat WTTJ
    CONTRACT_MAPPING = {
        "full_time": ContractType.CDI,
        "part_time": ContractType.OTHER,
        "fixed_term": ContractType.CDD,
        "temporary": ContractType.CDD,
        "internship": ContractType.STAGE,
        "apprenticeship": ContractType.ALTERNANCE,
        "freelance": ContractType.FREELANCE,
        "vie": ContractType.OTHER,
        "other": ContractType.OTHER,
    }

    # Mapping inverse pour les filtres
    CONTRACT_FILTER_MAPPING = {
        ContractType.CDI: ["full_time"],
        ContractType.CDD: ["temporary", "fixed_term"],
        # WTTJ regroupe CDD et intérim sous `temporary`. Le résultat est
        # désambiguïsé localement à partir du titre et du résumé.
        ContractType.INTERIM: ["temporary"],
        ContractType.STAGE: ["internship"],
        ContractType.ALTERNANCE: ["apprenticeship"],
        ContractType.FREELANCE: ["freelance"],
        ContractType.OTHER: ["part_time", "vie", "other"],
    }

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialise le scraper WTTJ.

        Args:
            config: Configuration optionnelle
        """
        super().__init__(config)
        self.hits_per_page = 20
        self._known_jobs: Dict[str, JobOffer] = {}
        self._details_cache: Dict[str, Optional[JobOffer]] = {}
        self._last_request_at: Optional[float] = None
        self.detail_failures = 0
        self.unavailable_jobs = 0
        self._session = requests.Session()
        retries = Retry(
            total=max(0, self.config.get("max_retries", 3) - 1),
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            respect_retry_after_header=True,
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retries))

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
        unknown_experience_count = 0
        self._known_jobs.clear()
        self._details_cache.clear()
        self.detail_failures = 0
        self.unavailable_jobs = 0

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
        cutoff = self._date_cutoff(criteria)
        index_name = (
            self.ALGOLIA_DATE_INDEX
            if criteria.sort_by == SortBy.DATE
            else self.ALGOLIA_INDEX
        )

        if use_geo:
            logger.info(f"Recherche WTTJ: query='{query}', coords={coords}, rayon={criteria.radius_km}km")
        elif criteria.location and criteria.radius_km:
            logger.warning(f"Géocodage échoué pour {criteria.location}, recherche sans rayon")
            logger.info(f"Recherche WTTJ: query='{query}'")
        else:
            logger.info(f"Recherche WTTJ: query='{query}'")

        while jobs_found < max_results:
            try:
                results = self._fetch_algolia(
                    query,
                    filters,
                    page,
                    coords,
                    radius_m,
                    index_name=index_name,
                )

                if not results.get("hits"):
                    logger.info("Plus d'offres trouvées")
                    break

                for hit in results["hits"]:
                    if jobs_found >= max_results:
                        break

                    job = self._parse_hit(
                        hit,
                        prefer_france=criteria.location.strip().casefold() == "france",
                    )
                    if job is None or job.id in seen_ids:
                        continue
                    # Dédupliquer avant tout appel de détail, même si l'offre
                    # est ensuite rejetée ou indisponible.
                    seen_ids.add(job.id)
                    self._known_jobs[job.id] = job
                    self._known_jobs[str(job.url)] = job
                    detailed = self.get_job_details(job.id)
                    if detailed and criteria.experience_levels and detailed.experience_level is None:
                        unknown_experience_count += 1
                    if detailed and self._matches_criteria(detailed, criteria, cutoff):
                        jobs_found += 1
                        yield detailed

                if jobs_found >= max_results:
                    break

                # Vérifier s'il y a plus de pages
                total_pages = results.get("nbPages", 1)
                if page >= total_pages - 1:
                    if results.get("nbHits", 0) > total_pages * self.hits_per_page:
                        logger.warning(
                            "WTTJ limite la pagination à {} offres sur {} correspondances",
                            total_pages * self.hits_per_page,
                            results["nbHits"],
                        )
                    break

                page += 1

            except (requests.RequestException, ValueError) as e:
                logger.error(f"Erreur recherche WTTJ: {e}")
                break

        logger.info(f"Recherche terminée: {jobs_found} offres uniques trouvées")
        if unknown_experience_count:
            logger.info(
                "WTTJ: {} offre(s) exclue(s), expérience non identifiable dans les détails",
                unknown_experience_count,
            )
        if self.detail_failures:
            logger.warning(
                "WTTJ: {} offre(s) non vérifiée(s), exclue(s) car les détails "
                "n'ont pas pu être récupérés. Les résultats peuvent être incomplets.",
                self.detail_failures,
            )

    def get_job_details(self, job_id: str) -> Optional[JobOffer]:
        """Charge les détails publics depuis une URL WTTJ ou un ID déjà rencontré.

        L'API publique du site expose le contenu intégral de la page, contrairement
        à Algolia. Aucune offre de liste n'est renvoyée en repli en cas d'échec.
        """
        listing = self._known_jobs.get(job_id)
        url = str(listing.url) if listing else job_id
        parts = urlsplit(url)
        match = re.fullmatch(r"/[^/]+/companies/([^/]+)/jobs/([^/]+)/?", parts.path)
        if (
            parts.scheme != "https"
            or parts.netloc not in ("www.welcometothejungle.com", "welcometothejungle.com")
            or match is None
        ):
            logger.warning("URL WTTJ invalide ou ID inconnu: {}", job_id)
            return None

        organization_slug, job_slug = match.groups()
        cache_key = f"{organization_slug}/{job_slug}"
        if cache_key in self._details_cache:
            cached = self._details_cache[cache_key]
            return cached.model_copy(deep=True) if cached else None

        endpoint = (
            f"{self.DETAILS_BASE_URL}/{quote(organization_slug, safe='')}"
            f"/jobs/{quote(job_slug, safe='')}"
        )
        detailed = None
        try:
            self._throttle()
            response = self._session.get(
                endpoint,
                headers={"Accept": "application/json"},
                timeout=self.config.get("timeout", 30),
            )
            if response.status_code in (404, 410):
                self.unavailable_jobs += 1
                logger.info("Offre WTTJ retirée: {}", url)
            else:
                response.raise_for_status()
                payload = response.json()
                data = payload.get("job") if isinstance(payload, dict) else None
                if not isinstance(data, dict):
                    raise ValueError("Réponse sans objet job")
                if data.get("archived_at") or data.get("status") not in (None, "published"):
                    self.unavailable_jobs += 1
                    logger.info("Offre WTTJ non publiée: {}", url)
                else:
                    detailed = self._parse_job_details(data, organization_slug, job_slug, listing)
        except (requests.RequestException, ValueError) as exc:
            self.detail_failures += 1
            logger.warning("Détails WTTJ non vérifiés pour {}: {}", url, exc)

        self._details_cache[cache_key] = detailed
        return detailed.model_copy(deep=True) if detailed else None

    def _throttle(self) -> None:
        """Espace tous les appels réseau, pas seulement les pages Algolia."""
        delay = max(0.0, float(self.config.get("delay", 1.0)))
        if self._last_request_at is not None:
            remaining = delay - (monotonic() - self._last_request_at)
            if remaining > 0:
                sleep(remaining)
        self._last_request_at = monotonic()

    @staticmethod
    def _html_text(html: Optional[str]) -> str:
        """Conserve les espaces des éléments inline et les limites des paragraphes."""
        if html is not None and not isinstance(html, str):
            raise ValueError("Contenu HTML de détail invalide")
        soup = BeautifulSoup(html or "", "lxml")
        for element in soup.select("script, style"):
            element.decompose()
        for element in soup.select("p, div, li, h1, h2, h3, h4, h5, h6, br"):
            element.insert_before("\n")
            element.insert_after("\n")
        lines = soup.get_text(" ", strip=False).splitlines()
        return "\n".join(" ".join(line.split()) for line in lines if line.strip())

    @staticmethod
    def _candidate_requirements(description: str, profile: str) -> str:
        """Préfère le profil candidat pour ne pas compter l'âge de l'entreprise."""
        if profile:
            return profile
        sections = re.split(
            r"(?im)^\s*(?:votre profil|profil recherché|qualifications|"
            r"your profile|requirements|what you bring|who you are)\s*:?\s*$",
            description,
        )
        requirements = sections[-1] if len(sections) > 1 else description
        return re.split(
            r"(?im)^\s*(?:processus de recrutement|recruitment process|"
            r"nos avantages|pourquoi nous rejoindre|about us|benefits)\s*:?\s*$",
            requirements,
        )[0]

    def _parse_job_details(
        self,
        data: dict,
        organization_slug: str,
        job_slug: str,
        listing: Optional[JobOffer] = None,
    ) -> JobOffer:
        """Fusionne les métadonnées de liste et les champs intégraux de l'offre."""
        description = self._html_text(data.get("description"))
        profile = self._html_text(data.get("profile"))
        process = self._html_text(data.get("recruitment_process"))
        if not description and not profile:
            raise ValueError("Description et profil absents des détails")

        organization_data = data.get("organization") or {}
        if not isinstance(organization_data, dict):
            raise ValueError("Organisation de détail invalide")
        organization = dict(organization_data)
        organization["slug"] = organization_slug
        if listing and not organization.get("name"):
            organization["name"] = listing.company
        hit = dict(data)
        hit.update(
            reference=(
                listing.id.removeprefix("wttj_") if listing
                else data.get("wttj_reference") or data.get("reference")
            ),
            name=data.get("name") or (listing.title if listing else None),
            slug=data.get("slug") or job_slug,
            organization=organization,
            offices=data.get("offices") or ([data["office"]] if data.get("office") else []),
            # Le résumé ne doit pas remplacer la description intégrale.
            summary="",
        )
        level = data.get("experience_level")
        if isinstance(level, str):
            minimum = re.match(r"(?:MORE_THAN_)?(\d+)", level)
            if minimum:
                hit["experience_level_minimum"] = int(minimum.group(1))
        job = self._parse_hit(hit, prefer_france=True)
        if job is None:
            raise ValueError("Métadonnées de détail invalides")
        if listing:
            if job.experience_level is None:
                job.experience_level = listing.experience_level
            if not data.get("published_at"):
                job.posted_at = listing.posted_at
            if not hit["offices"]:
                job.location = listing.location
            if not data.get("contract_type"):
                job.contract_type = listing.contract_type
        job.description = "\n\n".join(text for text in (description, profile, process) if text)
        inferred = experience_level_from_description(
            self._candidate_requirements(description, profile)
        )
        if inferred is not None:
            job.experience_level = inferred
        if data.get("contract_type") == "temporary":
            contract_text = f"{job.title} {description} {profile}".casefold()
            if re.search(r"\bint[ée]rim(?:aire)?\b", contract_text):
                job.contract_type = ContractType.INTERIM
        return job

    def _fetch_algolia(
        self,
        query: str,
        filters: str,
        page: int,
        coords: Optional[Tuple[float, float]] = None,
        radius_m: Optional[int] = None,
        index_name: Optional[str] = None,
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
        index = index_name or self.ALGOLIA_INDEX
        url = f"https://{self.ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/{index}/query"

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

        self._throttle()
        response = self._session.post(
            url, headers=headers, json=payload, timeout=self.config.get("timeout", 30)
        )
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
                filters.append(f"({' OR '.join(dict.fromkeys(contract_filters))})")

        # Le niveau indiqué par WTTJ peut contredire l'exigence chiffrée dans
        # les détails. Filtrer sur ce champ ici supprimerait les vrais matchs.

        if criteria.location.strip().casefold() == "france":
            filters.append("offices.country_code:FR")

        cutoff = self._date_cutoff(criteria)
        if cutoff:
            filters.append(f"published_at_timestamp>={int(cutoff.timestamp())}")

        if criteria.companies:
            company_filters = [
                f"organization.name:{self._quote_filter_value(company)}"
                for company in criteria.companies
            ]
            filters.append(f"({' OR '.join(company_filters)})")

        for company in criteria.exclude_companies:
            filters.append(
                f"NOT organization.name:{self._quote_filter_value(company)}"
            )

        return " AND ".join(filters) if filters else ""

    @staticmethod
    def _quote_filter_value(value: str) -> str:
        """Protège une valeur textuelle utilisée dans un filtre Algolia."""
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    def _date_cutoff(criteria: SearchCriteria) -> Optional[datetime]:
        """Retourne la date minimale demandée par les critères temporels."""
        days = None
        if criteria.date_posted == DatePosted.PAST_24H:
            days = 1
        elif criteria.date_posted == DatePosted.PAST_WEEK:
            days = 7
        elif criteria.date_posted == DatePosted.PAST_MONTH:
            days = 30
        elif not criteria.date_posted and criteria.posted_within_days:
            days = criteria.posted_within_days

        if days is None:
            return None
        return datetime.now(timezone.utc) - timedelta(days=days)

    def _matches_criteria(
        self,
        job: JobOffer,
        criteria: SearchCriteria,
        cutoff: Optional[datetime] = None,
    ) -> bool:
        """Applique les critères qui nécessitent une validation après parsing."""
        if criteria.contract_types and job.contract_type not in criteria.contract_types:
            return False

        if (
            criteria.location.strip().casefold() == "france"
            and job.location.rsplit(",", 1)[-1].strip().casefold() != "france"
        ):
            return False

        if (
            criteria.experience_levels
            and job.experience_level not in criteria.experience_levels
        ):
            return False

        company = job.company.casefold()
        if criteria.companies and company not in {
            value.casefold() for value in criteria.companies
        }:
            return False
        if company in {value.casefold() for value in criteria.exclude_companies}:
            return False

        cutoff = cutoff or self._date_cutoff(criteria)
        if cutoff:
            if job.posted_at is None:
                return False
            posted_at = job.posted_at
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
            if posted_at < cutoff:
                return False

        return True

    def _parse_hit(self, hit: dict, prefer_france: bool = False) -> Optional[JobOffer]:
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
                office = next(
                    (
                        office for office in offices
                        if prefer_france and office.get("country_code") == "FR"
                    ),
                    offices[0],
                )
                city = office.get("city", "")
                country_code = office.get("country_code", "")
                country = office.get("country") or (
                    "France" if country_code == "FR" else country_code
                )
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
            if contract_wttj == "temporary":
                contract_text = f"{title} {hit.get('summary') or ''}".casefold()
                if "intérim" in contract_text or "interim" in contract_text:
                    contract_type = ContractType.INTERIM

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
            if contract_type == ContractType.STAGE:
                experience_level = ExperienceLevel.INTERNSHIP
            elif exp_min is not None:
                exp_min = float(exp_min)
                if exp_min <= 1:
                    experience_level = ExperienceLevel.JUNIOR
                elif exp_min <= 3:
                    experience_level = ExperienceLevel.MID
                elif exp_min <= 6:
                    experience_level = ExperienceLevel.SENIOR
                elif exp_min <= 10:
                    experience_level = ExperienceLevel.LEAD
                else:
                    experience_level = ExperienceLevel.DIRECTOR

            # La description textuelle est plus précise que le niveau déclaré.
            inferred_experience = experience_level_from_description(description)
            if inferred_experience == ExperienceLevel.JUNIOR:
                experience_level = inferred_experience
            elif inferred_experience == ExperienceLevel.SENIOR and experience_level not in (
                ExperienceLevel.LEAD,
                ExperienceLevel.DIRECTOR,
            ):
                experience_level = inferred_experience

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
