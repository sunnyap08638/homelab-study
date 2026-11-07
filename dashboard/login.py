"""Read the dashboard account or set it interactively as the Ubuntu operator."""
from base64 import b64encode
from getpass import GetPassWarning, getpass
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import warnings

CONFIG = Path.home() / ".config/homelab-dashboard"


def credentials(username, password):
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", username):
        raise ValueError("Use 1–64 letters, digits, or . _ @ - in the username.")
    if (not isinstance(password, str) or not re.fullmatch(r"[ -~]{16,128}", password)
            or not password.strip()):
        raise ValueError("Use a strong passphrase or generated password of 16–128 printable ASCII characters.")
    return b"Basic " + b64encode(f"{username}:{password}".encode("ascii"))


def authorization():
    try:
        account = json.loads((CONFIG / "account.json").read_text())
    except FileNotFoundError:
        return credentials("homelab", (CONFIG / "password").read_text().strip())
    return credentials(account["username"], account["password"])


def main():
    username = input("New Mount Olympus username: ").strip()
    # Refuse to read a password if the terminal cannot hide it.
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        password = getpass("New Mount Olympus password: ")
        if password != getpass("Confirm password: "):
            raise ValueError("Passwords did not match. The account was not changed.")
    credentials(username, password)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(dict(username=username, password=password), file)
            file.flush()
            temporary.replace(CONFIG / "account.json")
        finally:
            temporary.unlink(missing_ok=True)
    (CONFIG / "password").unlink(missing_ok=True)
    print("Mount Olympus account saved.")


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt, GetPassWarning):
        raise SystemExit("Account change canceled. Run this command in an interactive terminal.")
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
