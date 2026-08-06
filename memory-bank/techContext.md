# Tech Context: PDTBuddy

## Technologies Used

### Backend
| Technology | Version/Details | Purpose |
|---|---|---|
| Python | 3.x (uses `str \| None` union syntax → 3.10+) | Primary language |
| Flask | Latest | Web framework |
| Waitress | Latest | Production WSGI server |
| flask-login | Latest | User session/auth management |
| flask-session | Latest | Server-side filesystem sessions |
| mysql-connector-python | Latest | MySQL database connectivity |
| ldap3 | Latest | Qualcomm LDAP authentication |
| pandas | Latest | Data manipulation for reports |
| itsdangerous | Latest | Signed tokens for result IDs |
| markupsafe | Latest | Safe HTML markup |
| PyInstaller | Latest | `.exe` packaging |

### AI/LLM Integration
| Technology | Purpose |
|---|---|
| QGenie SDK (`qgenie`) | Qualcomm internal LLM API client |
| QGenie Chat SDK (`qgeniechat_core`) | QGenie Chat with internal search |
| ChatWise API | Alternative LLM via `chatwise.qualcomm.com` |

### External APIs / Services
| Service | URL | Purpose |
|---|---|---|
| Qualcomm LDAP | `qed-ldap.qualcomm.com:636` (SSL) | Authentication |
| Orbit (QIPL) | Configured via `ORBIT_ENDPOINT_QIPL` | CR data (Hyderabad) |
| Orbit (SD) | Configured via `ORBIT_ENDPOINT_SD` | CR data (San Diego) |
| Orbit (CH) | Configured via `ORBIT_ENDPOINT_CH` | CR data (China) |
| Jira | `jira-dc2.qualcomm.com/jira/` | Issue tracking |
| Axiom | Internal time-series DB | Job summary data |
| QDT | Internal | Testing system |

### Frontend
| Technology | Purpose |
|---|---|
| Jinja2 | Server-side HTML templating |
| Bootstrap (likely) | CSS framework (inferred from templates) |
| Vanilla JS / jQuery | Client-side interactivity |
| Static files | `static/css/`, `static/js/`, `static/img/` |

### Database
| Component | Details |
|---|---|
| MySQL | Primary database server |
| Main schema | `pdt_stats_dashboard` |
| Per-BU schemas | Mapped via `BU_DATABASE_MAPPING` config |
| Key tables | `cr_master`, `cr_master_search`, `job_summary`, per-target MTBF tables |

### Optional Components
| Component | Details |
|---|---|
| MCP MTBF Server | `mcp_mtbf_server.py`, FastMCP, SSE transport, port 8765 |
| Axiom Poller | `scripts/axiom_poller.py`, continuous job data polling |

## Development Setup

### Running the Application
```bash
# Development (Flask dev server)
python app.py

# Production (Waitress, auto-selected)
python app.py  # Waitress used if installed

# Default: http://127.0.0.1:500
# Override: BUDDY_HOST=0.0.0.0 BUDDY_PORT=8080 python app.py
```

### Environment Variables
| Variable | Default | Purpose |
|---|---|---|
| `BUDDY_HOST` | `127.0.0.1` | Server bind host |
| `BUDDY_PORT` | `500` | Server bind port |
| `FLASK_SERVER_NAME` | (none) | Flask SERVER_NAME config |
| `FLASK_PREFERRED_URL_SCHEME` | `http` | URL scheme |
| `QGENIE_RESULT_CACHE_DIR` | `/var/tmp/qgenie_result_cache` | Result cache directory |
| `RESULT_CACHE_TTL_SEC` | `3600` | Cache TTL in seconds |
| `MCP_MTBF_ENABLED` | `0` | Enable MCP MTBF server |
| `MCP_MTBF_PORT` | `8765` | MCP server port |
| `MCP_MTBF_HOST` | `0.0.0.0` | MCP server host |
| `ORBIT_SD_IP_PREFIXES` | (none) | IP prefixes for SD region |
| `ORBIT_QIPL_IP_PREFIXES` | (none) | IP prefixes for QIPL region |
| `ORBIT_CH_IP_PREFIXES` | (none) | IP prefixes for CH region |

### Configuration File (`config.py`)
Key configuration items:
- `SECRET_KEY` — Flask secret key
- `REPORT_GENERATION_CONFIG` — Jira/CR base URLs, report settings
- `ADMIN_USERS` — list of admin usernames
- `BYPASS_USERS` — users who bypass certain checks
- `USERS_DB_PATH` — path to users database
- `TARGET_GROUP`, `SD_TARGET_GROUP`, `CH_TARGET_GROUP`, `CH_STABILITY_GROUP` — LDAP group names
- `ORBIT_ENDPOINT_QIPL/SD/CH` — Orbit API endpoints per region
- `MYSQL_HOST/PORT/USER/PASSWORD` — MySQL connection details
- `MAIN_DATABASE_NAME` — primary MySQL schema
- `BU_DATABASE_MAPPING` — dict mapping BU keys to MySQL schemas
- `BU_ICONS` — dict mapping BU keys to icon paths
- `QGENIE_TEXT_TO_SQL_MODEL` — model for chatbot text-to-SQL
- `QGENIE_HIGHLIGHTS_MODEL` — default model for CR highlights
- `QGENIE_HIGHLIGHTS_MODEL_OPTIONS` — list of available models

### Build Process
```bash
# Build .exe with PyInstaller
run_build.bat
# or
python build_help.py
```

### Data Ingestion
```bash
# Run data ingest (CRs, Jiras from Orbit/Jira into MySQL)
python run_ingest.py

# Update Axiom job summary
python scripts/update_axiom_job_summary.py
python scripts/update_axiom_job_summary_full.py

# Run weekly summary generation
python run_weekly_summary.py
```

## Technical Constraints

### Authentication
- All non-public routes require valid Qualcomm LDAP session
- LDAP server: `qed-ldap.qualcomm.com:636` (SSL, port 636)
- User DN format: `uid={username},ou=people,dc=qualcomm,dc=com`
- Group membership checked via LDAP for BU access control

### Session Storage
- Sessions stored on filesystem (`flask_session/` directory)
- Session files must be writable by the app process
- `temp_reports/` directory created at startup for report files

### Database
- MySQL connection required at startup
- `pdt_stats_dashboard` schema must exist with `cr_master`, `cr_master_search` tables
- Per-BU schemas must exist for target data access

### Network
- Orbit API access requires internal Qualcomm network
- Jira API access requires internal Qualcomm network
- QGenie API requires valid API key per user session
- Axiom access requires JWT authentication

## Tool Usage Patterns

### MySQL Utilities (`src/utils.py`)
- `get_mysql_connection_db()` — get connection to main DB
- `execute_and_fetch_all(conn, sql, params)` — safe query execution
- `execute_and_fetch_one_or_zero(conn, sql, params)` — single row fetch
- `sanitize_column_name(name)` — prevent SQL injection in dynamic column names

### Orbit Client (`orbit_client.py`)
- `fetch_cr(cr_id, use_cache=True)` — fetch CR data from Orbit
- Uses session-stored `orbit_endpoint` for regional routing

### Axiom Client (`src/axiom_client.py`)
- Handles JWT authentication for Axiom API
- Used by scripts for job summary data ingestion

### Sync Central (`src/sync_central.py`)
- Coordinates data synchronization between external systems and MySQL