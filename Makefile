.PHONY: help build up down restart logs clean prod-build prod-up prod-down

# Default target
help:
	@echo "Available commands:"
	@echo "  Development:"
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

# Clean up containers, volumes, networks, and build cache
clean:
	docker-compose down -v
	docker network prune -f
	docker system prune -f

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
	docker-compose -f docker-compose.prod.yml --env-file prod.env build

prod-up:
	docker-compose -f docker-compose.prod.yml --env-file prod.env up -d

prod-down:
	docker-compose -f docker-compose.prod.yml --env-file prod.env down

prod-logs:
	docker-compose -f docker-compose.prod.yml --env-file prod.env logs -f

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
