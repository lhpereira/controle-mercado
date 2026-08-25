from __future__ import annotations

import re
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
MIN_PASSWORD_LENGTH = 8


class AuthError(ValueError):
    pass


def create_user(db: sqlite3.Connection, username: str, password: str) -> int:
    """Cria um usuário. O primeiro usuário criado herda os cupons órfãos."""

    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise AuthError(
            "Usuário deve ter de 3 a 32 caracteres (letras, números, ponto, traço ou sublinhado)."
        )
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")

    is_first_user = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    try:
        cursor = db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
    except sqlite3.IntegrityError as error:
        raise AuthError("Esse nome de usuário já está em uso.") from error

    user_id = int(cursor.lastrowid)
    if is_first_user:
        db.execute("UPDATE receipts SET user_id = ? WHERE user_id IS NULL", (user_id,))
        db.execute("UPDATE products SET user_id = ? WHERE user_id IS NULL", (user_id,))
    db.commit()
    return user_id


def verify_user(db: sqlite3.Connection, username: str, password: str) -> sqlite3.Row | None:
    user = db.execute(
        "SELECT * FROM users WHERE username = ?", ((username or "").strip(),)
    ).fetchone()
    if user and check_password_hash(user["password_hash"], password or ""):
        return user
    return None
