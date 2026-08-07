"""
Authentication routes Blueprint.
Extracted from app.py — login, logout, post-login flow (QGenie gate, team selection).
"""
import logging
from datetime import datetime

from flask import (
    Blueprint, render_template, request, session, redirect,
    url_for, flash, current_app
)
from flask_login import (
    login_user, login_required, logout_user, current_user, login_fresh
)

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    from app import (
        authenticate_ldap_user, _set_orbit_session, is_user_in_group,
        log_user_activity, User,
        ADMIN_USERS, BYPASS_USERS, TARGET_GROUP, SD_TARGET_GROUP,
    )

    if request.method == 'GET' and current_user.is_authenticated:
        if not login_fresh():
            session.clear()
            logout_user()
            flash("Please sign in again.", "warning")
            return render_template("login.html")
        if session.get('viewer_mode'):
            return redirect(url_for('live_status_publish_bp.landing'))
        if session.get('needs_qgenie_before_team_selection'):
            return redirect(url_for('auth.post_login_qgenie_gate'))
        if session.get('needs_team_selection'):
            return redirect(url_for('auth.post_login_team_selection'))
        return redirect(url_for('cr_overview_embed'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip().lower()
        # Accept Qualcomm email format at login
        if username.endswith('@qti.qualcomm.com'):
            username = username.split('@', 1)[0].strip()
        password = request.form.get('password') or ''
        remember_me = False

        try:
            if not username:
                flash("Username is required.", "danger")
                return render_template("login.html")

            if not password:
                flash("Password is required.", "danger")
                return render_template("login.html")

            print(f"[LOGIN] LDAP auth attempt for: {username}", flush=True)

            if not authenticate_ldap_user(username, password):
                log_user_activity(
                    user_id=username,
                    action_type="LOGIN",
                    result_status="FAILURE",
                    error_message="Invalid Qualcomm username/password"
                )
                print(f"[LOGIN] LDAP auth failed for: {username}", flush=True)
                flash("Invalid Qualcomm username or password.", "danger")
                return render_template("login.html")

            print(f"[LOGIN] LDAP auth success for: {username}", flush=True)

            # Detect orbit endpoint (QIPL=HYD / SD) and store in session
            _set_orbit_session(username)

            try:
                _login_target_group = is_user_in_group(username, TARGET_GROUP)
            except Exception as _login_tg_err:
                print(f"[LOGIN] Early TARGET_GROUP check error for {username} in '{TARGET_GROUP}': {_login_tg_err}", flush=True)
                _login_target_group = False

            if _login_target_group:
                user = User.get(username)
                login_user(user, remember=remember_me)
                log_user_activity(user_id=username, action_type="LOGIN", result_status="SUCCESS", user_type='internal')
                flash(f"Welcome {username}!", "success")
                _now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[LOGIN] TARGET_GROUP user sent directly to internal PDT Buddy: {username}  |  {_now}", flush=True)
                session['login_time'] = datetime.now().timestamp()
                session['last_active'] = datetime.now().timestamp()
                session.pop('needs_team_selection', None)
                session.pop('needs_qgenie_before_team_selection', None)
                session.pop('viewer_mode', None)
                if not session.get('qgenie_api_key'):
                    session['needs_qgenie_popup'] = True
                else:
                    session.pop('needs_qgenie_popup', None)
                session.modified = True
                return redirect(url_for('bu_selection'))

            if username in BYPASS_USERS:
                user = User.get(username)
                login_user(user, remember=remember_me)
                log_user_activity(user_id=username, action_type='LOGIN', result_status='SUCCESS', user_type='external')
                session['login_time'] = datetime.now().timestamp()
                session['last_active'] = datetime.now().timestamp()
                session.pop('needs_qgenie_popup', None)
                session['viewer_mode'] = True
                session.modified = True
                flash(f'Welcome {username}! (viewer mode)', 'success')
                return redirect(url_for('live_status_publish_bp.landing'))

            # Admin check
            if username in ADMIN_USERS:
                user = User.get(username)
                login_user(user, remember=remember_me)
                log_user_activity(user_id=username, action_type="LOGIN", result_status="SUCCESS", user_type='internal')
                flash("Admin login successful.", "success")
                _now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[LOGIN] Admin logged in: {username}  |  {_now}", flush=True)
                session['login_time'] = datetime.now().timestamp()
                session['last_active'] = datetime.now().timestamp()
                session.pop('viewer_mode', None)
                if not session.get('qgenie_api_key'):
                    session['needs_qgenie_popup'] = True
                session.modified = True
                return redirect(url_for('bu_selection'))

            # Regular user group check — load dynamic privileges
            try:
                from src.admin_milestone_routes import _load_user_privileges
                from config import LIVE_STATUS_VIEWER_GROUP_ACCESS
                _priv = _load_user_privileges()
                _dyn_admins = set(_priv.get('admins', []))
                _viewers = set(_priv.get('viewers', []))
                _dynamic_extra_groups = _priv.get('extra_groups', [])
                _live_status_extra_groups = list((LIVE_STATUS_VIEWER_GROUP_ACCESS or {}).keys()) if isinstance(LIVE_STATUS_VIEWER_GROUP_ACCESS, dict) else []
                _extra_groups = list(dict.fromkeys(
                    str(g or '').strip()
                    for g in list(_dynamic_extra_groups or []) + _live_status_extra_groups
                    if str(g or '').strip()
                ))
                print(f"[LOGIN] Dynamic privileges loaded for {username}: admins={username.lower() in _dyn_admins}, viewer={username.lower() in _viewers}, extra_groups={len(_extra_groups)}", flush=True)
            except Exception as _priv_err:
                print(f"[LOGIN] Dynamic privileges load failed for {username}: {_priv_err}", flush=True)
                _dyn_admins = _viewers = set()
                _extra_groups = []

            try:
                _in_target_group = is_user_in_group(username, TARGET_GROUP)
            except Exception as _tg_err:
                print(f"[LOGIN] TARGET_GROUP check error for {username} in '{TARGET_GROUP}': {_tg_err}", flush=True)
                _in_target_group = False

            # Also treat SD group members as full internal users
            _in_sd_group = False
            try:
                _in_sd_group = is_user_in_group(username, SD_TARGET_GROUP)
            except Exception as _sd_err:
                print(f"[LOGIN] SD_TARGET_GROUP check error for {username} in '{SD_TARGET_GROUP}': {_sd_err}", flush=True)
            if _in_sd_group:
                print(f"[LOGIN] SD group member detected for {username} -> granting full internal access", flush=True)
            _in_target_group = _in_target_group or _in_sd_group

            # Check dynamic admin
            if username.lower() in _dyn_admins:
                user = User(id=username, role='admin')
                login_user(user, remember=remember_me)
                log_user_activity(user_id=username, action_type="LOGIN", result_status="SUCCESS", user_type='internal')
                flash(f"Welcome {username}! (admin)", "success")
                session['login_time'] = session['last_active'] = datetime.now().timestamp()
                session.pop('viewer_mode', None)
                session.modified = True
                return redirect(url_for('bu_selection'))

            # Check viewer
            if username.lower() in _viewers and not _in_target_group:
                user = User(id=username, role='viewer')
                login_user(user, remember=remember_me)
                log_user_activity(user_id=username, action_type="LOGIN", result_status="SUCCESS",
                                   error_message="viewer list login", user_type='external')
                flash(f"Welcome {username}! (viewer)", "success")
                session['login_time'] = session['last_active'] = datetime.now().timestamp()
                session.pop('needs_qgenie_popup', None)
                session['viewer_mode'] = True
                session.modified = True
                return redirect(url_for('live_status_publish_bp.landing'))

            # Check extra groups
            _extra_hits = []
            for _grp in _extra_groups:
                try:
                    if is_user_in_group(username, _grp):
                        _extra_hits.append(_grp)
                except Exception as _grp_err:
                    print(f"[LOGIN] Extra group check error for {username} in '{_grp}': {_grp_err}", flush=True)
            _in_extra = bool(_extra_hits)

            print(f"[LOGIN] Group resolution for {username}: target_group={_in_target_group}, extra_group_match={_in_extra}, extra_group_hits={_extra_hits}", flush=True)

            if _in_target_group:
                user = User.get(username)
                login_user(user, remember=remember_me)
                log_user_activity(user_id=username, action_type="LOGIN", result_status="SUCCESS", user_type='internal')
                flash(f"Welcome {username}!", "success")
                _now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[LOGIN] TARGET_GROUP user sent directly to internal PDT Buddy:  {username}  |  {_now}", flush=True)
                session['login_time'] = datetime.now().timestamp()
                session['last_active'] = datetime.now().timestamp()
                session.pop('needs_team_selection', None)
                session.pop('needs_qgenie_before_team_selection', None)
                session.pop('viewer_mode', None)
                if not session.get('qgenie_api_key'):
                    session['needs_qgenie_popup'] = True
                else:
                    session.pop('needs_qgenie_popup', None)
                session.modified = True
                return redirect(url_for('bu_selection'))

            if _in_extra:
                user = User.get(username)
                login_user(user, remember=remember_me)
                log_user_activity(
                    user_id=username,
                    action_type="LOGIN",
                    result_status="SUCCESS",
                    error_message=f"Extra group external access: {', '.join(_extra_hits)}",
                    user_type='external'
                )
                flash(f"Welcome {username}!", "success")
                _now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[LOGIN] Extra-group user sent to external Live Status:  {username}  |  {_now}", flush=True)
                session['login_time'] = datetime.now().timestamp()
                session['last_active'] = datetime.now().timestamp()
                session.pop('needs_qgenie_popup', None)
                session['viewer_mode'] = True
                session.modified = True
                return redirect(url_for('live_status_publish_bp.landing'))
            else:
                # LDAP auth succeeded but no group match — fallback viewer
                user = User(id=username, role='viewer')
                login_user(user, remember=remember_me)
                log_user_activity(
                    user_id=username,
                    action_type="LOGIN",
                    result_status="SUCCESS",
                    error_message="LDAP success; fallback viewer login",
                    user_type='external'
                )
                print(f"[LOGIN] Fallback viewer login for {username}: LDAP success but no target-group match.", flush=True)
                flash(f"Welcome {username}! (viewer)", "success")
                session['login_time'] = session['last_active'] = datetime.now().timestamp()
                session.pop('needs_qgenie_popup', None)
                session['viewer_mode'] = True
                session.modified = True
                return redirect(url_for('live_status_publish_bp.landing'))

        except Exception as e:
            print(f"[LOGIN] Exception for {username}: {e}", flush=True)
            log_user_activity(
                user_id=username or "UNKNOWN",
                action_type="LOGIN",
                result_status="FAILURE",
                error_message=str(e)
            )
            flash("System error during login.", "danger")
            return render_template("login.html")

    return render_template("login.html")


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
@auth_bp.route('/logout')
@login_required
def logout():
    from app import log_user_activity
    _uid = getattr(current_user, "id", "unknown")
    _now = datetime.now()
    _now_str = _now.strftime('%Y-%m-%d %H:%M:%S')
    _login_ts = session.get('login_time')
    if _login_ts:
        _dur = int(_now.timestamp() - float(_login_ts))
        _h, _rem = divmod(_dur, 3600)
        _m, _s = divmod(_rem, 60)
        _dur_str = f"{_h}h {_m}m {_s}s"
    else:
        _dur_str = "unknown"
    print(f"[LOGOUT] User: {_uid}  |  Time: {_now_str}  |  Session duration: {_dur_str}", flush=True)
    log_user_activity(
        user_id=_uid,
        action_type="LOGOUT",
        result_status="SUCCESS",
        error_message=f"Session duration: {_dur_str}"
    )
    session.clear()
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


# ---------------------------------------------------------------------------
# Post-login: QGenie gate
# ---------------------------------------------------------------------------
@auth_bp.route("/post_login/qgenie", methods=["GET"])
@login_required
def post_login_qgenie_gate():
    """Require TARGET_GROUP users to configure QGenie before access-mode selection."""
    from app import is_user_in_group, TARGET_GROUP
    username = str(getattr(current_user, "id", "") or "").strip().lower()

    try:
        is_target_group_user = is_user_in_group(username, TARGET_GROUP)
    except Exception:
        is_target_group_user = False

    if not is_target_group_user:
        session.pop("needs_team_selection", None)
        session.pop("needs_qgenie_popup", None)
        session.pop("needs_qgenie_before_team_selection", None)
        session["viewer_mode"] = True
        session.modified = True
        return redirect(url_for('live_status_publish_bp.landing'))

    if session.get("qgenie_api_key"):
        session.pop("needs_qgenie_popup", None)
        session.pop("needs_qgenie_before_team_selection", None)
        session.modified = True
        return redirect(url_for('auth.post_login_team_selection'))

    session["needs_team_selection"] = True
    session["needs_qgenie_popup"] = True
    session["needs_qgenie_before_team_selection"] = True
    session.modified = True
    return render_template("qgenie_login_gate.html", target_group=TARGET_GROUP)


# ---------------------------------------------------------------------------
# Post-login: Team selection
# ---------------------------------------------------------------------------
@auth_bp.route("/post_login/team_selection", methods=["GET", "POST"])
@login_required
def post_login_team_selection():
    """TARGET_GROUP users choose Internal PDT Buddy or External Live Status after login."""
    from app import is_user_in_group, TARGET_GROUP
    username = str(getattr(current_user, "id", "") or "").strip().lower()

    if not session.get("needs_team_selection"):
        return redirect(url_for('live_status_publish_bp.landing'))

    try:
        is_target_group_user = is_user_in_group(username, TARGET_GROUP)
    except Exception:
        is_target_group_user = False

    if not is_target_group_user:
        session.pop("needs_team_selection", None)
        session.pop("needs_qgenie_popup", None)
        session.modified = True
        return redirect(url_for('live_status_publish_bp.landing'))

    if not session.get('qgenie_api_key'):
        session["needs_qgenie_popup"] = True
        session["needs_qgenie_before_team_selection"] = True
        session.modified = True
        return redirect(url_for('auth.post_login_qgenie_gate'))

    if request.method == "POST":
        choice = (request.form.get("team_type") or "").strip().lower()
        session.pop("needs_team_selection", None)

        if choice == "internal":
            session.pop("needs_qgenie_popup", None)
            session.modified = True
            return redirect(url_for('bu_selection'))

        if choice == "external":
            session.pop("needs_qgenie_popup", None)
            session.modified = True
            return redirect(url_for('live_status_publish_bp.landing'))

        flash("Please select Internal Team or External Team.", "warning")
        session["needs_team_selection"] = True
        session.modified = True

    return render_template("team_selection.html", target_group=TARGET_GROUP)