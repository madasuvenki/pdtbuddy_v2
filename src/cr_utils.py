"""
CR (Change Request) utility functions.
Extracted from app.py — CR data normalization, JIRA counts, overall CR summaries.
"""
import logging
import time as _time
import traceback

logger = logging.getLogger(__name__)


def get_overall_crs_summary(target_name: str, get_target_info_fn, fq_table_fn, get_conn_fn) -> dict:
    """
    Read summary metrics from <target>_overallcrs.
    Retries up to 3 times on MySQL error 1412 (table definition changed).

    Args:
        target_name: Target key
        get_target_info_fn: Callable(target_name) -> info dict
        fq_table_fn: Callable(target_name, suffix) -> fully-qualified table name
        get_conn_fn: Callable() -> MySQL connection
    """
    info = get_target_info_fn(target_name)
    if not info:
        raise ValueError(f"Target '{target_name}' not found")

    overall_table = fq_table_fn(target_name, "overallcrs")
    last_err = None
    for _attempt in range(3):
        conn = get_conn_fn()
        if not conn:
            raise RuntimeError("Database connection failed")
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total_crs,
                    SUM(CASE WHEN reported_team = 'PDT_Reported' THEN 1 ELSE 0 END) AS pdt_reported,
                    SUM(CASE WHEN reported_team = 'PDT_Unique' THEN 1 ELSE 0 END) AS pdt_unique,
                    SUM(CASE WHEN reported_team = 'OtherTeam Reported' THEN 1 ELSE 0 END) AS other_team_reported
                FROM {overall_table}
                """
            )
            row = cur.fetchone() or {}

            total_crs = int(row.get("total_crs") or 0)
            pdt_reported = int(row.get("pdt_reported") or 0)
            pdt_unique = int(row.get("pdt_unique") or 0)
            other_team_reported = int(row.get("other_team_reported") or 0)
            pdt_unique_pct = round((pdt_unique * 100.0 / pdt_reported), 2) if pdt_reported else 0.0

            return {
                "target_name": target_name,
                "target_display": info.get("display_name") or target_name,
                "sp_name": info.get("sp_name") or "",
                "total_crs": total_crs,
                "pdt_reported": pdt_reported,
                "pdt_unique": pdt_unique,
                "other_team_reported": other_team_reported,
                "pdt_unique_pct": pdt_unique_pct,
            }
        except Exception as e:
            last_err = e
            err_str = str(e)
            if '1412' in err_str or 'Table definition has changed' in err_str:
                logger.warning(f"get_overall_crs_summary: 1412 retry {_attempt+1}/3 for '{target_name}'")
                try: cur.close()
                except Exception: pass
                try: conn.close()
                except Exception: pass
                _time.sleep(0.3 * (_attempt + 1))
                continue
            raise
        finally:
            try: cur.close()
            except Exception: pass
            try: conn.close()
            except Exception: pass
    raise RuntimeError(f"Table definition changed after 3 retries for '{target_name}': {last_err}")


def fetch_cr_jira_counts(target: str, cr_ids: list, get_target_info_fn, get_schema_fn, get_conn_fn) -> dict:
    """
    For a given target and list of CR IDs, fetch from <prefix>_jiras:
    - device_count: COUNT(DISTINCT serial_no)
    - mcn_count: COUNT(DISTINCT mcn)
    Returns: dict[cr_id_str] = {"device_count": int, "mcn_count": int}
    """
    if not cr_ids:
        return {}

    info = get_target_info_fn(target)
    if not info:
        return {}

    schema_name = get_schema_fn(target)
    if not schema_name:
        return {}

    prefix = str(info.get("db_prefix", target)).lower()
    jiras_table = f"`{schema_name}`.`{prefix}_jiras`"

    unique_crs = sorted({str(c) for c in cr_ids if c})

    conn = get_conn_fn()
    if not conn:
        return {}
    cur = conn.cursor(dictionary=True)
    try:
        placeholders = ",".join(["%s"] * len(unique_crs))
        sql = f"""
            SELECT
                cr,
                COUNT(DISTINCT serial_no) AS device_count,
                COUNT(DISTINCT mcn)       AS mcn_count
            FROM {jiras_table}
            WHERE cr IN ({placeholders})
            GROUP BY cr
        """
        cur.execute(sql, unique_crs)
        rows = cur.fetchall() or []
        out = {}
        for r in rows:
            cr_val = r.get("cr")
            if not cr_val:
                continue
            out[str(cr_val)] = {
                "device_count": int(r.get("device_count") or 0),
                "mcn_count": int(r.get("mcn_count") or 0),
            }
        return out
    except Exception as e:
        logger.error(f"fetch_cr_jira_counts failed: {e}")
        logger.debug(traceback.format_exc())
        return {}
    finally:
        cur.close()
        conn.close()


def normalize_cr_rows_for_table(rows, jira_counts_by_cr=None):
    """
    Normalize raw unique_crs rows into an enriched CR table format.

    Data columns only (no S.No. - template adds that):
    CR, CR Title, Occurrence, CR Age, CR Area, CR Subsystem, CR Functionality,
    CR Date, Image, CR Status, PDT Priority, Last JIRA date,
    Device Count, MCN Count
    """
    jira_counts_by_cr = jira_counts_by_cr or {}
    normalized = []
    for r in rows:
        cr_id = r.get("cr") or r.get("mapped_cr")
        cr_id_str = str(cr_id) if cr_id is not None else None

        cr_title = (
            r.get("cr_title")
            or r.get("CR Title")
            or r.get("title")
            or r.get("CRTITLE")
        )

        occ = (
            r.get("cr_occurrence")
            or r.get("CR Occurrence")
            or r.get("CR_OCCURRENCE")
            or r.get("CR_Occurrence")
        )

        age = (
            r.get("cr_age")
            or r.get("CR Age")
            or r.get("age")
            or r.get("age_days")
        )

        cr_area = (
            r.get("cr_area")
            or r.get("CR_Area")
            or r.get("CR Area")
        )

        cr_subsystem = r.get("cr_subsystem")
        cr_functionality = r.get("cr_functionality")
        cr_date = r.get("cr_date")
        image = r.get("image")

        status = (
            r.get("cr_status")
            or r.get("CR Status")
            or r.get("status")
        )

        priority = (
            r.get("pdt_priority_tag")
            or r.get("PDT Priority")
            or r.get("priority")
        )

        last_jira = (
            r.get("jira_date__last_instance")
            or r.get("JIRA Date")
            or r.get("jira_date")
        )

        jc = jira_counts_by_cr.get(cr_id_str, {}) if cr_id_str else {}
        device_count = jc.get("device_count", 0)
        mcn_count = jc.get("mcn_count", 0)

        normalized.append({
            "CR": cr_id,
            "CR Title": cr_title,
            "Occurrence": occ,
            "CR Age": age,
            "CR Area": cr_area,
            "CR Subsystem": cr_subsystem,
            "CR Functionality": cr_functionality,
            "CR Date": cr_date,
            "Image": image,
            "CR Status": status,
            "PDT Priority": priority,
            "Last JIRA date": last_jira,
            "Device Count": device_count,
            "MCN Count": mcn_count,
        })
    return normalized
