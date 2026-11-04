# CraftCore Minecraft dashboard

A Linux-hosted Vanilla Minecraft server manager with a private browser dashboard, guided first-run setup, verified downloads, backups, and a saved-world map.

## Run

Requires Linux, Python 3.10+, Bash, and an installed Java runtime compatible with your chosen Minecraft version. Minecraft and Java are not bundled.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
./start.sh
```

The dashboard listens on **127.0.0.1:3000**. For a remote host:

```bash
ssh -N -L 3000:127.0.0.1:3000 user@your-server
```

Open http://127.0.0.1:3000/setup and enter the one-time token printed in the server terminal. Create an administrator with a password of at least 12 characters. Select a stable release (or explicitly enable snapshots), check Java, choose settings, and accept the Minecraft EULA. Setup can finish without starting Minecraft.

An existing server.jar can be imported without downloading a replacement or restarting Minecraft. Private configuration is backed up before import. Existing offline-mode settings are preserved; fresh installs authenticate Minecraft accounts by default.

## Versions and Java

Versions come from Mojang's official manifest, cached for one hour with an explicit Refresh action. Only versions with server downloads appear. Older versions load in pages. Catalog changes never automatically upgrade a server. A cached catalog is labeled when offline; uncached version metadata still needs internet access. Java is user-managed: enter `java` or an absolute executable path. Setup checks the minimum required major version; older Minecraft releases may have additional Java compatibility constraints.

Use Installation settings in the dashboard to change versions. Stop Minecraft first. CraftCore requires a successful verified world backup, blocks downgrades, verifies the official download size and SHA-1, and retains server.previous.jar. Snapshots are experimental. Backup files must be retained before manually rolling a world back; an older jar alone cannot undo world-format changes.

## Configuration and secrets

Non-secret settings are in `.state/installation.json`. Administrator credentials are scrypt hashes; sessions last eight hours and are invalidated on dashboard restart. Tokens, credentials, drafts, and catalog caches stay under ignored `.state/`. Files are private to the service account. Bootstrap tokens are also private: do not publish terminal output or dashboard logs. If administrator access is lost, stop the dashboard, back up and remove `.state/admin.json`, then rerun `./server.sh gui` to claim it again from the host terminal.

Copy `.env.example` to `.env` for optional Minecraft management/RCON and Playit secrets. Environment values take precedence over `.env`. Runtime `server.properties`, Playit files, EULA acceptance, player files, worlds, backups, and binaries are never committed. Keep the management and RCON interfaces disabled unless specifically needed.

## Controls

`./start.sh` opens setup on a new install; after setup it starts the dashboard and Minecraft/Gatekeeper. `./server.sh start|stop|restart|sleep|status|console` controls the game. `gui` and `gui-stop` control the dashboard. `all-stop` stops all locally managed processes. Playit is optional: supply its Linux binaries, run `tunnel-setup`, then `tunnel-start` after configuring a tunnel for the public game port. It is not started automatically. Do not run a second agent if your system already manages one.

The browser map shows saved terrain, not unsaved in-memory changes. It decodes palette-based Anvil chunks (modern format, with legacy palette packing support); pre-palette numeric-ID worlds and LZ4/external chunks are not rendered. Region revisions refresh every 15 seconds while the map is open. World export/import require Minecraft to be stopped. Background jobs do not block HTTP responses.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Publication must use a new repository made from the reviewed source snapshot, never the original deployment's history. No GitHub upload is performed by setup. The dashboard is intended for a trusted host account and SSH-forwarded access; do not expose its HTTP port to the public internet.

## World explorer

The map opens at the spawn coordinates saved in level.dat and offers angled surface relief, top-down viewing, quarter-turn rotation, a height cutoff, fullscreen, saved player markers, and waypoints. Drag or use arrow keys to pan; scroll, pinch, or use + / - to zoom. Press 0 or use Spawn to recenter. Camera preferences are saved in the browser. Nether starts below the bedrock roof at Y=120. Empty dimensions show an empty state.

This is sampled height relief, not full block models, textures, transparent interiors, or a live connection to game memory. Terrain and player positions follow world saves, with metadata checked every 15 seconds. Detail adapts from 32 to 2 blocks per sample as you zoom in. Hover coordinates are approximate to the sample footprint.

The authenticated GET /api/map/terrain endpoint accepts dim (overworld, the_nether/nether, the_end/end), rx, rz, step (2/4/8/16/32), and layer (-64 through 512). It returns a row-major square heights array and matching packed 24-bit RGB colors; -32768 denotes missing terrain. The payload includes size, step, region coordinates, revision, and readable/skipped chunk counts. Revisions follow region modification timestamps. Partial or concurrently saved reads are not cached. Up to 256 derived terrain tiles are kept in private .state/map-cache, outside world backups. Work is bounded to two terrain workers and runs off the HTTP loop.

The original PNG tile endpoint remains available. /api/map/data retains its existing response; terrain_only=1 skips decoding in-game map-item images when the new terrain viewer does not need them.
