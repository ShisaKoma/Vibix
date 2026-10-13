# ViBix

Back-office multi-site en cours de construction et site public de démonstration connecté à son API.

Le site Angular est un prototype séparé du back-office. Il consomme les routes publiques de l’API Django ; il ne remplace pas l’interface d’administration.

## Démarrage local — ViBix

Prérequis : Docker avec Compose, Python 3 et `make`.

```sh
cd vibix--django-app
make init
make up
make superuser
```

Ouvrir <http://localhost:8010/> ou <http://localhost:8010/admin/>. `make init` génère les secrets locaux dans `.env`. Créer le superutilisateur de manière interactive ; aucun compte ou mot de passe par défaut n’est fourni. 

Pour arrêter les services tout en conservant les données :

```sh
make down
```

Ne pas utiliser `docker compose down -v` si les volumes doivent être conservés.

## Site Angular (POC)

Lancer ViBix, puis dans un autre terminal :

```sh
cd poc--angular-site
cp .env.example .env
docker compose up -d --build
```

Le site est disponible sur <http://localhost:8020/>. Par défaut, il interroge l’API de ViBix sur le port `8010` et utilise le site d’identifiant `2`. Adapter ces valeurs dans `.env` au besoin. Le mode de données fictives s’active explicitement avec `DATA_MODE=mock`. 

## Déploiement

Le déploiement préparé concerne le socle Django. Il se fait avec le Compose autonome [`compose.production.yaml`](vibix--django-app/compose.production.yaml), et non avec la configuration locale. Sur l’hôte de déploiement, préparer `vibix--django-app/.env.production` avec des secrets propres à l’environnement, les hôtes autorisés et les origines HTTPS, puis exécuter depuis `vibix--django-app/` :

```sh
docker compose --env-file .env.production -f compose.production.yaml build web
docker compose --env-file .env.production -f compose.production.yaml up -d db
docker compose --env-file .env.production -f compose.production.yaml run --rm web python manage.py migrate --noinput
docker compose --env-file .env.production -f compose.production.yaml run --rm web python manage.py check --deploy
docker compose --env-file .env.production -f compose.production.yaml up -d web
```

