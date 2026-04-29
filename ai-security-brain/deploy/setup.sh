#!/bin/bash
set -euo pipefail

# ── AI Security Brain — EC2 Setup Script ─────────────────────────────────────
# Run on a fresh Ubuntu 22.04+ EC2 instance (t3.medium recommended).
# Security group: allow ports 22, 80, 443.

echo "=== AI Security Brain — Server Setup ==="

# ── Install Docker ─────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "Docker installed. You may need to log out and back in for group changes."
else
    echo "Docker already installed."
fi

# ── Install Docker Compose plugin ──────────────────────────────────────────────
if ! docker compose version &>/dev/null; then
    echo "Installing Docker Compose plugin..."
    sudo apt-get update -qq
    sudo apt-get install -y docker-compose-plugin
else
    echo "Docker Compose already installed."
fi

# ── Clone or update repo ──────────────────────────────────────────────────────
REPO_DIR="$HOME/ai-security-brain"
if [ -d "$REPO_DIR" ]; then
    echo "Updating existing repo..."
    cd "$REPO_DIR" && git pull
else
    echo "Cloning repo..."
    # Replace with your actual repo URL
    git clone https://github.com/YOUR_ORG/ai-security-brain.git "$REPO_DIR"
    cd "$REPO_DIR"
fi

# ── Generate .env if not present ───────────────────────────────────────────────
ENV_FILE="$REPO_DIR/deploy/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "Generating production .env file..."
    cp "$REPO_DIR/deploy/.env.example" "$ENV_FILE"

    # Auto-generate secrets
    PG_PASS=$(openssl rand -hex 16)
    CH_PASS=$(openssl rand -hex 16)
    ADMIN_PASS=$(openssl rand -hex 12)
    JWT=$(openssl rand -hex 32)
    ROBOT_KEY=$(openssl rand -hex 16)

    sed -i "s/CHANGE_ME_strong_password_here/$PG_PASS/" "$ENV_FILE"
    sed -i "s/CHANGE_ME_clickhouse_password/$CH_PASS/" "$ENV_FILE"
    sed -i "s/CHANGE_ME_admin_password/$ADMIN_PASS/" "$ENV_FILE"
    sed -i "s/CHANGE_ME_generate_with_openssl_rand_hex_32/$JWT/" "$ENV_FILE"
    sed -i "s/CHANGE_ME_robot_api_key/$ROBOT_KEY/" "$ENV_FILE"

    echo ""
    echo "=== Generated credentials ==="
    echo "Admin email:    admin@aisecuritybrain.com"
    echo "Admin password: $ADMIN_PASS"
    echo "Robot API key:  $ROBOT_KEY"
    echo ""
    echo "Saved to: $ENV_FILE"
    echo ">>> IMPORTANT: Update DOMAIN in $ENV_FILE to your actual domain."
else
    echo ".env file already exists — skipping generation."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit deploy/.env — set DOMAIN to your domain (e.g. demo.aisecuritybrain.com)"
echo "  2. Point your domain's DNS A record to this server's public IP"
echo "  3. Start the platform:"
echo "     cd $REPO_DIR/deploy"
echo "     docker compose -f docker-compose.prod.yml up --build -d"
echo "  4. Caddy will auto-provision SSL via Let's Encrypt"
echo "  5. Open https://YOUR_DOMAIN to access the dashboard"
