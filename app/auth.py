import json
import uuid
import hashlib
import secrets
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

USERS_DIR = Path(__file__).parent / "users"
USERS_DIR.mkdir(exist_ok=True)


def _index_path() -> Path:
    return USERS_DIR / "_index.json"


def _user_path(user_id: str) -> Path:
    return USERS_DIR / f"{user_id}.json"


def _load_index() -> dict:
    p = _index_path()
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def _save_index(index: dict):
    with open(_index_path(), "w") as f:
        json.dump(index, f, indent=2)


def _hash_password(password: str, salt: str = None):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return dk.hex(), salt


def _safe(user: dict) -> dict:
    return {k: v for k, v in user.items() if k not in ("password_hash", "salt")}


def get_all_users() -> list:
    users = []
    for p in USERS_DIR.glob("*.json"):
        if p.name == "_index.json":
            continue
        with open(p) as f:
            users.append(_safe(json.load(f)))
    return sorted(users, key=lambda u: u.get("created_at", ""))


def create_user(username: str, password: str) -> dict:
    username = username.strip()
    if not username or not password:
        raise ValueError("Username and password are required")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")

    index = _load_index()
    if username.lower() in [u.lower() for u in index]:
        raise ValueError("Username already taken")

    user_id = str(uuid.uuid4())
    password_hash, salt = _hash_password(password)
    is_admin = (len(index) == 0)

    user = {
        "user_id": user_id,
        "username": username,
        "password_hash": password_hash,
        "salt": salt,
        "is_admin": is_admin,
        "created_at": datetime.utcnow().isoformat(),
    }

    with open(_user_path(user_id), "w") as f:
        json.dump(user, f, indent=2)

    index[username] = user_id
    _save_index(index)
    return _safe(user)


def authenticate(username: str, password: str) -> dict | None:
    index = _load_index()
    uid = None
    for uname, u_id in index.items():
        if uname.lower() == username.strip().lower():
            uid = u_id
            break
    if not uid:
        return None

    p = _user_path(uid)
    if not p.exists():
        return None
    with open(p) as f:
        user = json.load(f)

    expected, _ = _hash_password(password, user["salt"])
    if not secrets.compare_digest(expected, user["password_hash"]):
        return None
    return _safe(user)


def get_user_by_id(user_id: str) -> dict | None:
    p = _user_path(user_id)
    if not p.exists():
        return None
    with open(p) as f:
        return _safe(json.load(f))


def delete_user(user_id: str):
    p = _user_path(user_id)
    if not p.exists():
        return
    with open(p) as f:
        user = json.load(f)
    p.unlink()
    index = _load_index()
    index.pop(user.get("username", ""), None)
    _save_index(index)


def set_admin(user_id: str, is_admin: bool):
    p = _user_path(user_id)
    if not p.exists():
        return
    with open(p) as f:
        user = json.load(f)
    user["is_admin"] = is_admin
    with open(p, "w") as f:
        json.dump(user, f, indent=2)
