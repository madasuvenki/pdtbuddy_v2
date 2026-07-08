# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
import os

mysql_datas, mysql_binaries, mysql_hiddenimports = collect_all('mysql.connector')

_mysql_binaries = []
for src, dst in [
    ('venv/Lib/site-packages/_mysql_connector.cp313-win_amd64.pyd', '.'),
    ('venv/Lib/site-packages/libmysql.dll', '.'),
    ('venv/Lib/site-packages/mysql/vendor/plugin/mysql_native_password.dll', 'mysql/vendor/plugin'),
]:
    if os.path.exists(src):
        _mysql_binaries.append((src, dst))

_datas = [
    ('.env', '.'),
    ('src', 'src'),
    ('scripts', 'scripts'),
]
for optional_dir in ('config', 'docs', 'templates', 'static'):
    if os.path.exists(optional_dir):
        _datas.append((optional_dir, optional_dir))

_a = Analysis(
    ['scripts/update_axiom_job_summary.py'],
    pathex=['.'],
    binaries=_mysql_binaries + mysql_binaries,
    datas=_datas + mysql_datas,
    hiddenimports=[
        'dotenv', 'dotenv.main',
        'mysql', 'mysql.connector', 'mysql.connector.plugins',
        'mysql.connector.plugins.mysql_native_password',
        'mysql.connector.plugins.caching_sha2_password',
        'mysql.connector.plugins.sha256_password',
        'mysql.connector.plugins.mysql_clear_password',
        '_mysql_connector',
        'requests', 'urllib3', 'certifi', 'charset_normalizer',
        'http.client', 'ssl', 'json', 'decimal',
        'config',
        'src', 'src.utils',
        'scripts',
        'scripts.fetch_axiom_combined',
        'scripts.backfill_hwpdt_certicom_playlist',
    ] + mysql_hiddenimports,
    hookspath=[],
    runtime_hooks=['pyi_rth_syspath.py'],
    excludes=[
        'tkinter', 'matplotlib', 'scipy', 'notebook', 'IPython', 'pytest',
        'PyQt5', 'PyQt6', 'wx', 'flask', 'flask_login', 'flask_session',
        'waitress', 'ldap3',
    ],
    noarchive=False,
    optimize=0,
)

_pyz = PYZ(_a.pure)

exe = EXE(
    _pyz,
    _a.scripts,
    _a.binaries,
    _a.datas,
    [],
    name='UpdateAxiomJobSummary',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['libmysql.dll', '_mysql_connector*.pyd'],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
