#!/usr/bin/env bash
set -e
umask 077

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "========================================================"
echo "          PLAYIT.GG MINECRAFT TUNNEL SETUP              "
echo "========================================================"
echo ""
echo "[*] Generating a new claim code..."
CODE=$(./playit claim generate)
URL=$(./playit claim url "$CODE")

echo ""
echo "========================================================"
echo " PLEASE OPEN THIS URL IN YOUR WEB BROWSER TO CLAIM:"
echo ""
echo "    $URL"
echo ""
echo "========================================================"
echo ""
echo "[*] Waiting for you to claim the agent in your browser..."
echo "    (After clicking Claim, add a tunnel for 'Minecraft Java' on port 25565)"
echo ""

SECRET=$(./playit claim exchange "$CODE" --wait 300)

echo ""
echo "[✓] Agent claimed successfully!"
cat << TOML > "$DIR/playit.toml"
# Playit configuration
secret = "$SECRET"
TOML

echo "[✓] Configuration saved to playit.toml"
echo ""
echo "Now you can start the tunnel anytime with:"
echo "    ./server.sh tunnel-start"
echo ""
