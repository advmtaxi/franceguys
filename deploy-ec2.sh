#!/bin/bash

# Exit on any error
set -e

REPO_URL="https://github.com/advmtaxi/franceguys.git"
CLONE_DIR="franceguys"

echo "Deploying Dulo API Wrapper to EC2..."

# Check if docker is installed
if ! command -v docker &> /dev/null; then
    echo "Docker is not installed. Please install Docker first."
    exit 1
fi

# Clone or update the repository
if [ -d "$CLONE_DIR" ]; then
    echo "Repository already exists. Pulling latest changes..."
    cd $CLONE_DIR
    git pull origin main
else
    echo "Cloning repository..."
    git clone $REPO_URL
    cd $CLONE_DIR
fi

# Build and start the container using Docker Compose
echo "Starting the application with Docker Compose..."
# Use docker compose (v2) or docker-compose (v1)
if docker compose version > /dev/null 2>&1; then
    docker compose up -d --build
elif command -v docker-compose &> /dev/null; then
    docker-compose up -d --build
else
    echo "Docker compose not found, using plain Docker to build and run..."
    docker build -t dulo-api .
    docker rm -f dulo_api 2>/dev/null || true
    docker run -d --name dulo_api -p 6767:6767 --restart unless-stopped dulo-api
fi

echo "Deployment complete! API is running on port 6767."
echo "Test it with: curl -X POST http://localhost:6767/api/source -H 'Content-Type: application/json' -d '{\"type\":\"movie\", \"tmdbId\":969681}'"
