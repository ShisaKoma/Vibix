.PHONY: init up down logs migrate structure demo-poc superuser test check lint shell frontend
frontend:
	cd frontend && pnpm install --frozen-lockfile && pnpm build
init:
	python3 scripts/init_env.py
up:
	docker compose build web
	docker compose up -d db
	docker compose run --rm web python manage.py migrate --noinput
	docker compose run --rm web python manage.py seed_structural_data --all-sites
	docker compose up -d web
down:
	docker compose down
logs:
	docker compose logs -f web db
migrate:
	docker compose run --rm web python manage.py migrate --noinput
	docker compose run --rm web python manage.py seed_structural_data --all-sites
superuser:
	docker compose exec web python manage.py createsuperuser
test:
	docker compose run --rm web python manage.py test --noinput
check:
	docker compose run --rm web python manage.py check
	docker compose run --rm web python manage.py makemigrations --check --dry-run
lint:
	docker compose run --rm web ruff check .
	docker compose run --rm web ruff format --check .
shell:
	docker compose exec web python manage.py shell

structure:
	docker compose exec web python manage.py seed_structural_data --all-sites
demo-poc:
	docker compose exec web python manage.py seed_poc_demo --site-code demo
