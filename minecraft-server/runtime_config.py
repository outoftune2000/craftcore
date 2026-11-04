"""Load local secrets without executing shell code."""
import os
import json
from pathlib import Path
BASE = Path(__file__).resolve().parent
SECRET_PROPERTIES = {
    "management-server-secret": "MC_MANAGEMENT_SECRET",
    "rcon.password": "MC_RCON_PASSWORD",
    "management-server-tls-keystore-password": "MC_TLS_KEYSTORE_PASSWORD",
}
def load_env():
    path = BASE / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
def prepare_properties():
    load_env()
    path = BASE / "server.properties"
    if not path.exists():
        path.write_text((BASE / "server.properties.example").read_text())
    lines = path.read_text().splitlines()
    props = {line.split("=", 1)[0]: i for i, line in enumerate(lines) if "=" in line and not line.startswith("#")}
    for key, variable in SECRET_PROPERTIES.items():
        if variable not in os.environ:
            continue
        value = os.environ[variable]
        if any(c in value for c in "\r\n\\"):
            raise ValueError(variable + " contains unsupported characters")
        entry = key + "=" + value
        if key in props:
            lines[props[key]] = entry
        else:
            lines.append(entry)
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
def prepare_playit():
    load_env()
    secret = os.environ.get("PLAYIT_SECRET", "")
    if secret:
        path = BASE / "playit.toml"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("secret = " + json.dumps(secret) + "\n")
        path.chmod(0o600)
if __name__ == "__main__":
    prepare_playit()
