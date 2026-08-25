from __future__ import annotations

from functools import wraps

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .db import get_db
from .services.auth import AuthError, create_user, verify_user

bp = Blueprint("auth", __name__)


def _safe_next(url: str | None) -> str | None:
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return None


@bp.before_app_request
def load_logged_in_user() -> None:
    user_id = session.get("user_id")
    g.user = (
        get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user_id is not None
        else None
    )


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if password != request.form.get("confirm_password", ""):
            flash("As senhas não coincidem.", "error")
        else:
            try:
                user_id = create_user(get_db(), request.form.get("username", ""), password)
            except AuthError as error:
                flash(str(error), "error")
            else:
                session.clear()
                session["user_id"] = user_id
                return redirect(url_for("main.dashboard"))
    return render_template("register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        user = verify_user(
            get_db(), request.form.get("username", ""), request.form.get("password", "")
        )
        if user is None:
            flash("Usuário ou senha inválidos.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            next_url = _safe_next(request.form.get("next"))
            return redirect(next_url or url_for("main.dashboard"))
    return render_template("login.html", next=_safe_next(request.args.get("next")) or "")


@bp.post("/logout")
def logout():
    session.clear()
    flash("Sessão encerrada.", "success")
    return redirect(url_for("auth.login"))
