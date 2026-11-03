"""Modèles de données pour les offres d'emploi."""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl


class ContractType(str, Enum):
    """Types de contrat."""
    CDI = "cdi"
    CDD = "cdd"
    INTERIM = "interim"
    STAGE = "stage"
    ALTERNANCE = "alternance"
    FREELANCE = "freelance"
    OTHER = "other"


class ExperienceLevel(str, Enum):
    """Niveaux d'expérience."""
    INTERNSHIP = "internship"  # Stage
    JUNIOR = "junior"          # Entry level
    MID = "mid"                # Associate
    SENIOR = "senior"          # Mid-Senior level
    LEAD = "lead"              # Director
    DIRECTOR = "director"      # Executive


class WorkplaceType(str, Enum):
    """Type de lieu de travail."""
    ON_SITE = "on_site"        # Sur site
    REMOTE = "remote"          # Télétravail
    HYBRID = "hybrid"          # Hybride


class DatePosted(str, Enum):
    """Filtre par date de publication."""
    PAST_24H = "past_24h"      # Dernières 24 heures
    PAST_WEEK = "past_week"    # Dernière semaine
    PAST_MONTH = "past_month"  # Dernier mois
    ANY_TIME = "any_time"      # Toutes dates


class SortBy(str, Enum):
    """Tri des résultats."""
    RELEVANCE = "relevance"    # Par pertinence
    DATE = "date"              # Par date (plus récent)


class JobOffer(BaseModel):
    """Modèle représentant une offre d'emploi."""

    # Identifiants
    id: str = Field(..., description="Identifiant unique de l'offre")
    source: str = Field(..., description="Source de l'offre (linkedin, indeed, etc.)")
    url: HttpUrl = Field(..., description="URL de l'offre")

    # Informations principales
    title: str = Field(..., description="Titre du poste")
    company: str = Field(..., description="Nom de l'entreprise")
    location: str = Field(..., description="Localisation du poste")

    # Détails optionnels
    description: Optional[str] = Field(None, description="Description complète du poste")
    salary_min: Optional[float] = Field(None, description="Salaire minimum")
    salary_max: Optional[float] = Field(None, description="Salaire maximum")
    salary_currency: str = Field(default="EUR", description="Devise du salaire")

    contract_type: Optional[ContractType] = Field(None, description="Type de contrat")
    experience_level: Optional[ExperienceLevel] = Field(None, description="Niveau d'expérience requis")
    remote: Optional[bool] = Field(None, description="Possibilité de télétravail")
    application_activity: Optional[str] = Field(
        None,
        description="Indication LinkedIn sur le nombre de candidatures ou de clics",
    )

    # Métadonnées
    posted_at: Optional[datetime] = Field(None, description="Date de publication")
    scraped_at: datetime = Field(default_factory=datetime.now, description="Date de scraping")

    # Informations supplémentaires
    skills: List[str] = Field(default_factory=list, description="Compétences requises")
    benefits: List[str] = Field(default_factory=list, description="Avantages proposés")

    class Config:
        """Configuration du modèle."""
        json_schema_extra = {
            "example": {
                "id": "linkedin_123456",
                "source": "linkedin",
                "url": "https://www.linkedin.com/jobs/view/123456",
                "title": "Développeur Python Senior",
                "company": "TechCorp",
                "location": "Paris, France",
                "description": "Nous recherchons un développeur Python...",
                "salary_min": 50000,
                "salary_max": 70000,
                "contract_type": "cdi",
                "experience_level": "senior",
                "remote": True,
                "skills": ["Python", "Django", "PostgreSQL"],
            }
        }


class SearchCriteria(BaseModel):
    """Critères de recherche d'offres d'emploi."""

    # Recherche principale
    keywords: List[str] = Field(default_factory=list, description="Mots-clés de recherche")
    title: Optional[str] = Field(None, description="Titre de poste exact")
    location: str = Field(default="France", description="Localisation recherchée")
    radius_km: Optional[int] = Field(None, description="Rayon de recherche en km (5, 10, 25, 50, 100)")

    # Filtres de contrat et expérience
    contract_types: List[ContractType] = Field(default_factory=list, description="Types de contrat")
    experience_levels: List[ExperienceLevel] = Field(default_factory=list, description="Niveaux d'expérience")

    # Lieu de travail
    workplace_types: List[WorkplaceType] = Field(default_factory=list, description="Types de lieu de travail")

    # Filtres temporels
    date_posted: Optional[DatePosted] = Field(None, description="Date de publication")

    # Entreprises
    companies: List[str] = Field(default_factory=list, description="Entreprises spécifiques")
    exclude_companies: List[str] = Field(default_factory=list, description="Entreprises à exclure")

    # Salaire
    salary_min: Optional[int] = Field(None, description="Salaire minimum souhaité")

    # Activité des candidatures (LinkedIn)
    exclude_over_100_applicants: bool = Field(
        default=False,
        description="Exclure les offres avec plus de 100 candidatures ou clics sur Postuler",
    )

    # Tri et pagination
    sort_by: SortBy = Field(default=SortBy.RELEVANCE, description="Tri des résultats")
    max_results: int = Field(default=100, description="Nombre maximum de résultats")

    # Compatibilité (deprecated, utiliser date_posted)
    posted_within_days: Optional[int] = Field(None, description="[Deprecated] Utiliser date_posted")
