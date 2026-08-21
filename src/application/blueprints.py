"""Centralized Flask blueprint registration.

Keeping feature wiring in one module lets ``app.py`` remain an entry point while
route implementations are migrated into focused domain modules.  Imports are
local to avoid circular imports during the incremental migration from legacy
root-level modules.
"""

from __future__ import annotations

from flask import Flask


def register_feature_blueprints(app: Flask) -> None:
    """Register all production feature blueprints in their established order."""
    from dashboard_routes import dashboard_bp
    from device_summary_api import device_summary_api_bp
    from src.orbit_cr_routes import orbit_cr_bp
    from src.cr_analysis_agent import cr_agent_bp
    from live_status_publish_routes import live_status_publish_bp
    from live_status_view_api import live_status_view_api_bp
    from live_view_stats_routes import live_view_stats_bp
    from automotive_live_view_stats_routes import automotive_live_view_stats_bp
    from wbc_live_view_stats_routes import wbc_live_view_stats_bp
    from others_live_view_stats_routes import others_live_view_stats_bp
    from auto_gen45_public_routes import public_auto_gen45_bp
    from auto_gen5_public_routes import public_auto_gen5_bp
    from core_deck_routes import core_deck_bp
    from jiraquery_api_routes import jiraquery_api_bp
    from orbit_public_api_routes import orbit_public_api_bp
    from weekly_summary_routes import weekly_summary_bp
    from sp_entry_routes import sp_entry_bp
    from src.admin_milestone_routes import admin_milestone_bp
    from src.admin_paths_routes import admin_paths_bp
    from src.cr_compare_service import cr_compare_bp
    from pdt_dept_routes import pdt_dept_bp
    from excel_sync_routes import excel_sync_bp

    blueprints = (
        dashboard_bp,
        device_summary_api_bp,
        orbit_cr_bp,
        cr_agent_bp,
        live_status_publish_bp,
        live_status_view_api_bp,
        live_view_stats_bp,
        automotive_live_view_stats_bp,
        wbc_live_view_stats_bp,
        others_live_view_stats_bp,
        public_auto_gen45_bp,
        public_auto_gen5_bp,
        core_deck_bp,
        jiraquery_api_bp,
        orbit_public_api_bp,
        weekly_summary_bp,
        sp_entry_bp,
        admin_milestone_bp,
        admin_paths_bp,
        cr_compare_bp,
        pdt_dept_bp,
        excel_sync_bp,
    )

    for blueprint in blueprints:
        app.register_blueprint(blueprint)