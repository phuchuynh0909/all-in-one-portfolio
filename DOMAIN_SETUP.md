# Custom Domain Setup Guide

This guide explains how to set up your portfolio application with custom domains.

## Overview

- **Frontend**: `phuchynh.xyz` (or `www.phuchynh.xyz`)
- **API**: `api.phuchynh.xyz`

## Prerequisites

1. **Domain Registration**: Register `phuchynh.xyz` with a domain registrar
2. **DNS Access**: Access to configure DNS records for your domain
3. **Server**: A server with a public IP address where you'll deploy

## DNS Configuration

Configure the following DNS records with your domain registrar:

```
Type    Name    Value                TTL
A       @       YOUR_SERVER_IP       300
A       www     YOUR_SERVER_IP       300
A       api     YOUR_SERVER_IP       300
```

Replace `YOUR_SERVER_IP` with your actual server's public IP address.

## Configuration

### 1. Set Up Environment Variables

Edit the `prod.env` file to configure your API URL:

```bash
# For local development (uses Nginx proxy)
VITE_API_BASE_URL=/api/v1

# For custom domain
VITE_API_BASE_URL=https://api.phuchynh.xyz/api/v1

# For IP-based access
VITE_API_BASE_URL=http://YOUR_SERVER_IP/api/v1
```

### 2. Alternative: Set Environment Variable Directly

```bash
# Export environment variable (overrides prod.env)
export VITE_API_BASE_URL=https://api.phuchynh.xyz/api/v1

# Or set it inline with the command
VITE_API_BASE_URL=https://api.phuchynh.xyz/api/v1 make prod-build
```

## Deployment Steps

### 1. Build and Start the Services

```bash
# Stop any existing containers
make prod-down

# Build with your configured API URL
make prod-build

# Start the services
make prod-up
```

### 2. Access Nginx Proxy Manager

1. Open `http://YOUR_SERVER_IP:81` in your browser
2. **Default Login:**
   - Email: `admin@example.com`
   - Password: `changeme`
3. **Change the default credentials immediately!**

### 3. Configure Proxy Hosts

#### For API (api.phuchynh.xyz):
1. Go to "Proxy Hosts" → "Add Proxy Host"
2. **Details Tab:**
   - Domain Names: `api.phuchynh.xyz`
   - Scheme: `http`
   - Forward Hostname/IP: `backend`
   - Forward Port: `8000`
   - Block Common Exploits: ✅
   - Websockets Support: ✅
3. **SSL Tab:**
   - SSL Certificate: "Request a new SSL Certificate"
   - Force SSL: ✅
   - HTTP/2 Support: ✅
   - Email: your-email@example.com
   - Agree to Let's Encrypt Terms: ✅

#### For Frontend (phuchynh.xyz):
1. Go to "Proxy Hosts" → "Add Proxy Host"
2. **Details Tab:**
   - Domain Names: `phuchynh.xyz`, `www.phuchynh.xyz`
   - Scheme: `http`
   - Forward Hostname/IP: `frontend`
   - Forward Port: `80`
   - Block Common Exploits: ✅
   - Websockets Support: ✅
3. **SSL Tab:**
   - SSL Certificate: "Request a new SSL Certificate"
   - Force SSL: ✅
   - HTTP/2 Support: ✅
   - Email: your-email@example.com
   - Agree to Let's Encrypt Terms: ✅

### 4. Test Your Setup

1. **API**: `https://api.phuchynh.xyz/api/v1/health`
2. **Frontend**: `https://phuchynh.xyz`

## Troubleshooting

### DNS Propagation
- DNS changes can take up to 48 hours to propagate
- Use `dig api.phuchynh.xyz` to check if DNS is resolving correctly

### SSL Certificate Issues
- Ensure ports 80 and 443 are open on your server
- Check that your domain is pointing to the correct IP
- Let's Encrypt requires HTTP validation

### Container Networking
```bash
# Check if containers are running
docker ps

# Check container logs
docker-compose -f docker-compose.prod.yml logs nginx-proxy-manager
docker-compose -f docker-compose.prod.yml logs backend
docker-compose -f docker-compose.prod.yml logs frontend

# Test internal connectivity
docker exec -it all-in-one-portfolio-nginx-proxy-manager-1 ping backend
```

## Security Considerations

1. **Change default NPM credentials immediately**
2. **Use strong passwords**
3. **Keep containers updated**
4. **Consider firewall rules**
5. **Monitor access logs**

## Updating the Application

When you update your code:

```bash
# Rebuild and restart
make prod-build
make prod-up
```

The proxy configuration will remain intact.
