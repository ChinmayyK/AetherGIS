#!/bin/bash
set -e # Exit on error

# =====================================================
# AetherGIS 2.0 — Cloud Sync & Redeploy Script
# Optimized for OCI ARM64 (Oracle Cloud)
# =====================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}🚀 Starting AetherGIS 2.0 Production Update...${NC}"

# 1. PERMISSION SHIELD
sudo chown -R $USER:$USER /opt/aethergis
git config --global --add safe.directory /opt/aethergis

cd /opt/aethergis

# 2. PULL CODE
echo -e "${GREEN}📥 [1/6] Syncing with GitHub (Main Branch)...${NC}"
git fetch origin main
git reset --hard origin/main

# 3. STOP SERVICES
echo -e "${GREEN}🛑 [2/6] Stopping containers...${NC}"
# Use sudo to ensure docker permissions on OCI
sudo docker compose down --remove-orphans

# 4. REBUILD SERVICES (Sequential to save memory)
echo -e "${GREEN}🏗️ [3/6] Rebuilding AI Engine (Backend + Worker)...${NC}"
sudo docker compose build --no-cache backend worker

echo -e "${GREEN}🏗️ [4/6] Rebuilding Frontend (Nginx/Vite)...${NC}"
sudo docker compose build --no-cache frontend

# 5. CLEAN UP (Prune build cache to save disk space)
echo -e "${GREEN}🧹 Cleaning up build artifacts...${NC}"
sudo docker system prune -af

# 6. START SERVICES
echo -e "${GREEN}🚀 [5/6] Starting services...${NC}"
sudo docker compose up -d

# 7. HEALTHCHECKS
echo -e "${GREEN}⌛ [6/6] Waiting for Services to be ready...${NC}"

# Check Postgres
echo -n "Postgres: "
until sudo docker compose exec postgres pg_isready -U aethergis -d aethergis > /dev/null 2>&1; do
  echo -n "."
  sleep 2
done
echo " ✅"

# Check Redis
echo -n "Redis:    "
until sudo docker compose exec redis redis-cli -a Aether_Redis_Secure_2026_! ping > /dev/null 2>&1; do
  echo -n "."
  sleep 2
done
echo " ✅"

echo -e "${GREEN}✨ UPDATE COMPLETE! AetherGIS is live.${NC}"
echo -e "Access the portal at: ${YELLOW}https://aethergis.civiclens.space${NC}"
echo -e "Monitor live logs with: ${YELLOW}sudo docker compose logs -f backend${NC}"
