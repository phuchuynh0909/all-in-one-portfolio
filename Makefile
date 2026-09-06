.PHONY: help build up down restart logs clean prod-build prod-up prod-down \
        prod-build-frontend prod-build-backend prod-build-workers \
        prod-rebuild-frontend prod-rebuild-backend prod-rebuild-workers \
        prune-build-cache lock-backend

PROD := docker-compose -f docker-compose.prod.yml --env-file prod.env

# COMPOSE_BAKE lets buildx dedupe the three worker services, which share one
# build definition and one image tag, into a single build.
#
# Exported by make rather than prefixed onto the command, because `VAR=value cmd`
# is POSIX shell syntax: on Windows, where make hands recipes to cmd.exe, it
# fails with "'COMPOSE_BAKE' is not recognized as an internal or external
# command". `export` has make itself put it in the recipe's environment, so the
# same line works under cmd.exe, PowerShell, Git Bash and a POSIX shell alike.
#
# Scoped to the build targets: up/down/logs/ps have nothing to bake, and a
# global export would also reach the dev `docker-compose build` targets, which
# is not what this comment is claiming to do.
PROD_BUILD_TARGETS := prod-build prod-build-frontend prod-build-backend \
                      prod-build-workers prod-rebuild-frontend \
                      prod-rebuild-backend prod-rebuild-workers
$(PROD_BUILD_TARGETS): export COMPOSE_BAKE := true

# Default target
help:
	@echo "Available commands:"
	@echo "  Development:"
	@echo "    make lock-backend - Regenerate backend/requirements.lock.txt"
	@echo "    make build       - Build all containers (dev)"
	@echo "    make up          - Start all containers (dev)"
	@echo "    make down        - Stop all containers"
	@echo "    make restart     - Restart all containers"
	@echo "    make logs        - View logs from all containers"
	@echo "    make clean       - Remove all containers and volumes"
	@echo ""
	@echo "  Production:"
	@echo "    make prod-build  - Build production containers"
	@echo "    make prod-up     - Start production containers"
	@echo "    make prod-down   - Stop production containers"
	@echo "    make build-frontend - Build frontend for production"

# Build the containers
build:
	docker-compose build

# Start the application
up:
	docker-compose up -d

# Stop the application
down:
	docker-compose down

# Restart the application
restart:
	docker-compose down
	docker-compose up -d

# View logs
logs:
	docker-compose logs -f

# Clean up containers, volumes and networks.
# Deliberately not `docker system prune`: that also drops the BuildKit cache,
# including the multi-GB uv/npm cache mounts, turning the next build cold.
# Use `make prune-build-cache` when you actually want that.
clean:
	docker-compose down -v
	docker network prune -f
	docker container prune -f
	docker image prune -f

# Drop the BuildKit layer cache and cache mounts. Next build is a cold one.
prune-build-cache:
	docker builder prune -af

# Regenerate backend/requirements.lock.txt from backend/requirements.txt.
# Resolved inside the lock image on purpose: the environment markers then
# match the target (linux, py3.14) and the TA-Lib C library is present for any
# package that needs to build to report its metadata. Resolving on the host
# would produce a lock for macOS/arm64 that does not describe the image.
#
# torch is resolved from the PyTorch CPU index. Left to PyPI, torch 2.13 on
# aarch64 resolves to the +cu130 build and drags in ~3.5 GB of CUDA (cuda-toolkit,
# cudnn, cusparselt, nccl, nvshmem) plus 652 MB of triton — all of it SBSA-tagged
# wheels for ARM *GPU servers*, which cannot run here (torch.cuda.is_available()
# is False). --emit-index-url writes the index into the lock so `uv pip install`
# can find the +cpu build; --index-strategy is a CLI flag and must be repeated
# there (see backend/Dockerfile).
#
# NOTE: this pins the image to CPU torch. Deploying to a real GPU host needs its
# own lock generated without these flags, not this one.
lock-backend:
	docker build --target lock -t portfolio-backend-builder:lock backend/
	docker run --rm -v "$(PWD)/backend:/out" portfolio-backend-builder:lock \
	  sh -c "cd /out && uv pip compile requirements.txt -o requirements.lock.txt \
	         --extra-index-url https://download.pytorch.org/whl/cpu \
	         --index-strategy unsafe-best-match --emit-index-url \
	         --custom-compile-command 'make lock-backend'"
	-docker rmi portfolio-backend-builder:lock

# Force clean everything including networks
clean-all:
	docker-compose down -v
	-docker network rm $$(docker network ls -q -f name=all-in-one-portfolio_appnet)
	docker system prune -f

# Development-specific commands
dev-backend:
	docker-compose up backend -d

dev-frontend:
	docker-compose up frontend -d

# Rebuild specific services
rebuild-backend:
	docker-compose build backend
	docker-compose up -d --force-recreate backend

rebuild-frontend:
	docker-compose build frontend
	docker-compose up -d --force-recreate frontend

# Individual service logs
backend-logs:
	docker-compose logs -f backend

frontend-logs:
	docker-compose logs -f frontend

# Production commands
prod-build:
	$(PROD) build

prod-up:
	$(PROD) up -d

prod-down:
	$(PROD) down

prod-logs:
	$(PROD) logs -f

# Normal per-service builds reuse the layer cache. A backend build with no cache
# recompiles TA-Lib from source and reinstalls torch/catboost/xgboost from
# scratch, so --no-cache lives on the prod-rebuild-* targets below instead.
prod-build-frontend:
	$(PROD) build frontend

prod-build-backend:
	$(PROD) build backend

# One build for all three workers: they share portfolio-worker:latest, so
# building only one of them no longer leaves the other two on a stale image.
prod-build-workers:
	$(PROD) build worker-tick-ingest

# Escape hatches for when the cache is genuinely suspect (base image moved,
# floating tag shifted). Slow by design.
prod-rebuild-frontend:
	$(PROD) build --no-cache frontend

prod-rebuild-backend:
	$(PROD) build --no-cache backend

prod-rebuild-workers:
	$(PROD) build --no-cache worker-tick-ingest

prod-npm:
	@echo "Access Nginx Proxy Manager at: http://localhost:81"
	@echo "Default login: admin@example.com / changeme"
	@echo "CHANGE THE DEFAULT PASSWORD IMMEDIATELY!"

prod-status:
	docker-compose -f docker-compose.prod.yml --env-file prod.env ps

# Build frontend for production locally
build-frontend:
	cd frontend && npm run build

# Preview production build locally
preview-frontend:
	cd frontend && npm run preview
