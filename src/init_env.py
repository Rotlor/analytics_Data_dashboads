"""Generate fresh local secrets without printing them or overwriting .env."""
import secrets
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main():
    target = PROJECT_ROOT / ".env"
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("DWH_PASSWORD", "REDASH_COOKIE_SECRET", "REDASH_SECRET_KEY"):
        text = text.replace(key + "=\n", key + "=" + secrets.token_hex(32) + "\n")
    with target.open("x", encoding="utf-8") as handle:
        handle.write(text)
    print("Created .env with fresh secrets. Add your HH_USER_AGENT and HH_ACCESS_TOKEN for network collection.")


if __name__ == "__main__":
    main()
