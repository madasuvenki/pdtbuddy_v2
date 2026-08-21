# -*- coding: utf-8 -*-
"""
PDT Department Config page — reads/writes PDT_ALL_TEAM_CONFIG.json.
Displays CH / SD / QIPL department lists, transfer lists, exclusions.
Admin-only edit + save.
"""
import io
import json
import os
import logging

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

pdt_dept_bp = Blueprint("pdt_dept_bp", __name__)

PDT_CONFIG_PATH = os.environ.get(
    "PDT_TEAM_CONFIG_PATH",
    r"\\sphere\pdtstats\CR_TAT_Script\PDT_Team_mem\PDT_ALL_TEAM_CONFIG.json",
)


def _load_config() -> dict:
    """Load the PDT team config JSON. Returns empty dict on error."""
    try:
        with io.open(PDT_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("[PDT_DEPT] Failed to load config: %s", e)
        return {}


def _save_config(data: dict) -> tuple[bool, str]:
    """Write the PDT team config JSON back to disk."""
    try:
        # Atomic write: write to temp then rename
        import tempfile, shutil
        dir_path = os.path.dirname(PDT_CONFIG_PATH)
        fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp", prefix="pdt_cfg_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            shutil.move(tmp, PDT_CONFIG_PATH)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise
        return True, "Saved successfully."
    except Exception as e:
        logger.error("[PDT_DEPT] Failed to save config: %s", e)
        return False, str(e)


def _is_admin():
    return getattr(current_user, "role", "user") == "admin"


@pdt_dept_bp.route("/pdt_department")
@login_required
def pdt_department():
    cfg = _load_config()
    return render_template(
        "pdt_department.html",
        cfg=cfg,
        is_admin=_is_admin(),
        config_path=PDT_CONFIG_PATH,
    )


@pdt_dept_bp.route("/api/pdt_department/config", methods=["GET"])
@login_required
def api_get_pdt_config():
    cfg = _load_config()
    return jsonify({"success": True, "config": cfg})


@pdt_dept_bp.route("/api/pdt_department/save", methods=["POST"])
@login_required
def api_save_pdt_config():
    if not _is_admin():
        return jsonify({"success": False, "message": "Admin only"}), 403

    data = request.get_json(silent=True) or {}
    cfg = data.get("config")
    if not isinstance(cfg, dict):
        return jsonify({"success": False, "message": "Invalid config payload"}), 400

    # Preserve _comment
    existing = _load_config()
    if "_comment" in existing and "_comment" not in cfg:
        cfg["_comment"] = existing["_comment"]

    ok, msg = _save_config(cfg)
    return jsonify({"success": ok, "message": msg})