# JobScraper

Agrégateur d'offres d'emploi en France. Recherchez simultanément sur LinkedIn, HelloWork, France Travail, Welcome to the Jungle et Adzuna (Indeed, Monster...).

## Fonctionnalités

- **5 sources d'emploi** : LinkedIn, HelloWork, France Travail, WTTJ, Adzuna
- **Recherche géolocalisée** : Filtrage par ville et rayon (5-100 km)
- **Filtres avancés** : Type de contrat, expérience, télétravail, date de publication
- **Export flexible** : JSON, CSV ou affichage tableau
- **CLI intuitive** : Interface en ligne de commande avec Rich

## Installation

```bash
# Cloner le repository
git clone https://github.com/zeffut/jobscraper.git
cd jobscraper

# Créer un environnement virtuel
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# ou .venv\Scripts\activate  # Windows

# Installer les dépendances
pip install -e .
```

## Configuration

Copiez le fichier d'exemple et modifiez selon vos besoins :

```bash
cp .env.example .env
```

### Adzuna (optionnel)

Pour utiliser Adzuna (agrégateur Indeed, Monster, etc.), obtenez des clés API gratuites sur [developer.adzuna.com](https://developer.adzuna.com/) et ajoutez-les dans `.env` :

```env
ADZUNA_APP_ID=votre_app_id
ADZUNA_APP_KEY=votre_app_key
```

## Utilisation

### Recherche simple

```bash
# Recherche "python" à Paris
jobscraper search -k python -l Paris

# Recherche "data engineer" en CDI
jobscraper search -k "data engineer" -c cdi

# Recherche avec rayon de 25km autour de Lyon
jobscraper search -k développeur -l Lyon -r 25
```

### Options de recherche

```bash
jobscraper search [OPTIONS]

Options:
  -k, --keywords TEXT       Mots-clés de recherche (peut être répété)
  -t, --title TEXT          Titre de poste exact
  -l, --location TEXT       Localisation (ville, région) [défaut: France]
  -r, --radius [5|10|25|50|100]  Rayon de recherche en km
  -c, --contract [cdi|cdd|stage|alternance|interim|freelance]  Type de contrat
  -e, --experience [internship|junior|mid|senior|lead|director]  Niveau d'expérience
  -w, --workplace [on_site|remote|hybrid]  Type de lieu de travail
  --date [past_24h|past_week|past_month|any_time]  Date de publication
  --sort [relevance|date]   Tri des résultats
  -n, --max-results INT     Nombre maximum de résultats [défaut: 50]
  -s, --source [linkedin|hellowork|francetravail|adzuna|wttj|all]  Sources
  -o, --output PATH         Fichier de sortie
  --format [json|csv|table] Format de sortie [défaut: table]
  -d, --details             Récupérer les détails complets
  --exclude-over-100-applicants
                            Exclure les offres LinkedIn avec plus de 100
                            candidatures ou clics sur Postuler
```

### Exemples avancés

```bash
# CDI senior en remote, export JSON
jobscraper search -k "data engineer" -c cdi -e senior -w remote -o jobs.json

# Recherche sur WTTJ uniquement
jobscraper search -k python -l Paris -s wttj

# Recherche sur toutes les sources
jobscraper search -k développeur -l Toulouse -s all

# Offres des 7 derniers jours, triées par date
jobscraper search -k devops --date past_week --sort date

# 100 résultats avec détails complets
jobscraper search -k react -n 100 --details -o react_jobs.json

# Exclure les offres LinkedIn avec plus de 100 candidatures ou clics
jobscraper search -k python -s linkedin --exclude-over-100-applicants -o jobs.json
```

### Lister les sources

```bash
jobscraper sources
```

## Sources supportées

| Source | Statut | Description |
|--------|--------|-------------|
| LinkedIn | Actif | Offres d'emploi LinkedIn |
| HelloWork | Actif | Portail d'emploi français |
| France Travail | Actif | Ex Pôle Emploi |
| WTTJ | Actif | Welcome to the Jungle (startups) |
| Adzuna | Actif* | Agrégateur (Indeed, Monster...) |

\* Nécessite des clés API gratuites

## Structure du projet

```
jobscraper/
├── src/jobscraper/
│   ├── cli.py              # Interface ligne de commande
│   ├── config.py           # Configuration
│   ├── models/
│   │   └── job.py          # Modèles de données
│   ├── scrapers/
│   │   ├── base.py         # Classe de base
│   │   ├── linkedin.py     # Scraper LinkedIn
│   │   ├── hellowork.py    # Scraper HelloWork
│   │   ├── francetravail.py # Scraper France Travail
│   │   ├── wttj.py         # Scraper WTTJ (Algolia)
│   │   └── adzuna.py       # Client API Adzuna
│   └── utils/
│       └── geocoding.py    # Géocodage pour recherche par rayon
├── .env.example            # Template de configuration
├── pyproject.toml          # Configuration du projet
└── requirements.txt        # Dépendances
```

## Développement

```bash
# Installer les dépendances de développement
pip install -e ".[dev]"

# Lancer les tests
pytest

# Formater le code
black src/
isort src/
```

## Contribution

Les contributions sont les bienvenues ! N'hésitez pas à ouvrir une issue ou une pull request.
