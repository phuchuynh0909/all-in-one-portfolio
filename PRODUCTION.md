# Production Deployment Guide

This guide covers how to build and deploy your portfolio application for production.

## Production Build Features

### Frontend Production Setup
- **Optimized builds** with Vite
- **Code splitting** for better loading performance
- **Static file serving** with Nginx
- **Gzip compression** enabled
- **Security headers** configured
- **Client-side routing** support
- **API proxy** to backend

### Docker Production Setup
- **Multi-stage builds** for smaller image sizes
- **Nginx Proxy Manager** for SSL and domain management
- **Production environment** variables
- **Health checks** and restart policies

## Quick Commands

### Local Production Build
```bash
# Build frontend for production
make build-frontend

# Preview production build locally
make preview-frontend
```

### Docker Production Deployment
```bash
# Build all production containers
make prod-build

# Start production environment
make prod-up

# View production logs
make prod-logs

# Stop production environment
make prod-down
```

## Manual Build Process

### 1. Frontend Production Build
```bash
cd frontend
npm run build
```

This creates an optimized build in the `frontend/dist/` directory.

### 2. Docker Production Build
```bash
# Build production containers
docker-compose -f docker-compose.prod.yml build

# Start production services
docker-compose -f docker-compose.prod.yml up -d
```

## Configuration

### Frontend Build Optimizations
- **Terser minification** for smaller JS bundles
- **Manual chunking** for vendor libraries (React, MUI, Charts)
- **No source maps** in production
- **Asset optimization** with cache headers

### Nginx Configuration
- **Gzip compression** for all text assets
- **Static file caching** (1 year for assets)
- **Security headers** (XSS, CSRF protection)
- **API proxying** to backend service
- **SPA routing** support

### Production Environment
- Backend runs without reload
- Frontend served as static files through Nginx
- All services have restart policies
- Nginx Proxy Manager for SSL/domain management

## Accessing the Application

After running `make prod-up`:

- **Application**: http://localhost (port 80)
- **Nginx Proxy Manager**: http://localhost:81
  - Default login: `admin@example.com` / `changeme`

## SSL Setup with Nginx Proxy Manager

1. Access Nginx Proxy Manager at http://localhost:81
2. Log in with default credentials
3. Add a new Proxy Host:
   - Domain: your-domain.com
   - Forward to: frontend:80
   - Enable SSL with Let's Encrypt

## Performance Optimizations

### Build Size Optimizations
- Vendor libraries chunked separately
- Tree-shaking removes unused code
- CSS and JS minified
- Assets compressed with gzip

### Runtime Optimizations
- Static files cached for 1 year
- API calls proxied (no CORS issues)
- Nginx serves static files efficiently
- Production React build (no dev tools)

## Troubleshooting

### Check logs
```bash
make prod-logs
```

### Rebuild specific service
```bash
docker-compose -f docker-compose.prod.yml build frontend
docker-compose -f docker-compose.prod.yml up -d --force-recreate frontend
```

### Access container shell
```bash
docker-compose -f docker-compose.prod.yml exec frontend sh
docker-compose -f docker-compose.prod.yml exec backend bash
```

## Environment Variables

### Frontend (.env.production)
- `VITE_API_BASE_URL=/api/v1` - API base URL (proxied through Nginx)

### Backend (Production)
- `APP_ENVIRONMENT=production`
- `DATABASE_URL=sqlite:///app/portfolio.db`

## Security Considerations

- No source maps in production
- Security headers configured
- API behind proxy (no direct exposure)
- HTTPS available through Nginx Proxy Manager
- No development ports exposed
