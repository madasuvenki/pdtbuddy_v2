#!/usr/bin/env python3
"""Diagnose Build Report by-build DB lookup without changing production routes.

Usage examples:
  py -3 tools/diagnose_build_lookup.py "SecaAU_IVI.LE.1.0.r1-00027-NON_SAFE_STD_PVM.LAGVM-1"
  py -3 tools/diagnose_build_lookup.py "SecaAU_IVI.LE.1.0.r1-00027-NON_SAFE_STD_PVM.LAGVM-1" --table-like nord_hgy_adas_5_1_9_0
  py -3 tools/diagnose_build_lookup.py "00027" --table-like nord_hgy --verbose

This script is intentionally read-only:
- reads dashboard metadata
- reads information_schema
- runs SELECT COUNT / SELECT DISTINCT / SELECT sample rows
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


try:
    import config as _config
    from dashboard_common import (
        fq_table_for_target,
        get_mysql_connection_db,
        get_schema_for_bu,
        get_schema_for_target,
        load_metadata_config,
    )
except Exception as exc:  # pragma: no cover - diagnostic startup output
    print(f"[FATAL] Could not import PDT Buddy modules: {exc}", file=sys.stderr)
    print(f"[INFO] ROOT={ROOT}", file=sys.stderr)
    raise


BUILD_COL_CANDIDATES = (
    "metabuild",
    "MetaBuild",
    "Metabuild",
    "meta_build",
    "Meta Build",
    "build_id",
    "Build ID",
    "BuildID",
    "build",
    "Build",
    "build_name",
    "Build Name",
    "builds",
    "CRM Build ID",
    "Meta-ID",
    "meta_id",
    "Matched Build",
    "matched_build",
    "software_product",
    "Software Product",
)
DATE_COL_CANDIDATES = (
    "jira_date",
    "Jira Date",
    "JiraDate",
    "created",
    "Created",
    "created_date",
    "fetched_date",
    "Fetched Date",
    "date",
    "Date",
)
DEVICE_COL_CANDIDATES = (
    "serial_no",
    "Serial No",
    "serial",
    "Serial",
    "serial_number",
    "Serial Number",
    "device_id",
    "Device ID",
    "chip_id",
    "Chip ID",
    "host_name",
    "Host Name",
    "mcn",
    "MCN",
)
TICKET_COL_CANDIDATES = (
    "stability_ticket",
    "Stability Ticket",
    "jira_key",
    "Jira Key",
    "JIRA Key",
    "jira",
    "Jira",
    "JIRA",
    "jira_id",
    "Jira ID",
    "key",
    "ticket",
)
TITLE_COL_CANDIDATES = (
    "jira_title",
    "Jira Title",
    "JIRA Title",
    "summary",
    "Summary",
    "title",
    "Title",
)
CR_COL_CANDIDATES = (
    "mapped_cr",
    "mapped_crs",
    "Mapped CR",
    "Mapped CRs",
    "cr",
    "CR",
    "cr_number",
    "crid",
)


def ser(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


def norm_build(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    # Support pasted UNC/local path and keep terminal folder/file only.
    return value.replace("/", "\\").split("\\")[-1].strip()


def norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def first_col(columns: Sequence[str], candidates: Sequence[str]) -> str:
    by_norm = {norm_col(c): c for c in columns or []}
    for cand in candidates:
        got = by_norm.get(norm_col(cand))
        if got:
            return got
    return ""


def quote_ident(name: str) -> str:
    return f"`{str(name or '').replace('`', '')}`"


def quote_fq(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def table_name_from_fq(fq_name: str) -> Tuple[str, str]:
    raw = str(fq_name or "").replace("`", "").strip()
    if "." in raw:
        schema, table = raw.split(".", 1)
        return schema.strip(), table.strip()
    return "", raw


def table_exists(cursor, schema: str, table: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema=%s AND table_name=%s LIMIT 1",
        (schema, table),
    )
    return cursor.fetchone() is not None


def table_columns(cursor, schema: str, table: str) -> List[str]:
    try:
        cursor.execute(f"SHOW COLUMNS FROM {quote_fq(schema, table)}")
        return [str(r.get("Field") or "") for r in (cursor.fetchall() or []) if r.get("Field")]
    except Exception as exc:
        print(f"    [ERR] SHOW COLUMNS failed for {schema}.{table}: {exc}")
        return []


def configured_schemas(cursor=None) -> List[str]:
    schemas = set()

    main = str(getattr(_config, "MAIN_DATABASE_NAME", "") or "").strip("` ")
    if main:
        schemas.add(main)

    mapping = getattr(_config, "BU_DATABASE_MAPPING", {}) or {}
    if isinstance(mapping, dict):
        for value in mapping.values():
            if isinstance(value, str):
                if value.strip():
                    schemas.add(value.strip("` "))
            elif isinstance(value, dict):
                for key in ("schema", "database", "db", "name"):
                    got = str(value.get(key) or "").strip("` ")
                    if got:
                        schemas.add(got)

    try:
        metadata = load_metadata_config(active_only=False) or {}
        for cfg in (metadata.get("TARGETS_CONFIG") or {}).values():
            bu = str((cfg or {}).get("bu") or "").strip().upper()
            schema = (
                str((cfg or {}).get("schema") or "").strip("` ")
                or (get_schema_for_bu(bu) or "")
            )
            if schema:
                schemas.add(schema.strip("` "))
    except Exception as exc:
        print(f"[WARN] Could not collect metadata schemas: {exc}")

    # Include all pdt_stats_* schemas if information_schema is reachable.
    # This is diagnostic-only and helps catch a missing BU_DATABASE_MAPPING entry.
    if cursor is not None:
        try:
            cursor.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name LIKE 'pdt\\_stats\\_%' ESCAPE '\\\\'"
            )
            for row in cursor.fetchall() or []:
                got = str(row.get("schema_name") or "").strip()
                if got:
                    schemas.add(got)
        except Exception as exc:
            print(f"[WARN] Could not expand pdt_stats_* schemas: {exc}")

    return sorted(schemas)


def target_candidates(target_hint: str = "") -> List[Dict[str, str]]:
    hint = str(target_hint or "").strip().lower()
    out: List[Dict[str, str]] = []
    seen = set()

    metadata = load_metadata_config(active_only=False) or {}
    targets = metadata.get("TARGETS_CONFIG") or {}
    for target_key, cfg in sorted(targets.items(), key=lambda item: str(item[0]).lower()):
        target = str(target_key or "").strip()
        if not target:
            continue
        display = str((cfg or {}).get("display_name") or target).strip()
        bu = str((cfg or {}).get("bu") or "").strip().upper()
        if hint and hint not in target.lower() and hint not in display.lower():
            continue

        schema = (
            get_schema_for_target(target)
            or get_schema_for_bu(bu)
            or str((cfg or {}).get("schema") or "").strip("`")
        )
        prefix = str(
            (cfg or {}).get("db_name")
            or (cfg or {}).get("db_prefix")
            or target
        ).strip("`.")
        if not schema or not prefix:
            continue

        key = (schema.lower(), prefix.lower(), target.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "target": target,
                "target_display": display,
                "bu": bu,
                "schema": schema.strip("`"),
                "prefix": prefix,
            }
        )
    return out


def count_build_rows(
    cursor,
    schema: str,
    table: str,
    build_col: str,
    date_col: str,
    device_col: str,
    ticket_col: str,
    title_col: str,
    cr_col: str,
    build: str,
    sample_limit: int,
) -> Dict[str, Any]:
    fq = quote_fq(schema, table)
    like = f"%{build}%"
    row: Dict[str, Any] = {}

    # Main count mirrors production route behavior.
    try:
        cursor.execute(
            "SELECT COUNT(*) AS cnt"
            + (
                f", MIN({quote_ident(date_col)}) AS min_date, MAX({quote_ident(date_col)}) AS max_date"
                if date_col
                else ", NULL AS min_date, NULL AS max_date"
            )
            + f" FROM {fq} WHERE {quote_ident(build_col)} LIKE %s",
            (like,),
        )
        row = cursor.fetchone() or {}
    except Exception as exc:
        return {"ok": False, "error": f"COUNT failed: {exc}"}

    cnt = int(row.get("cnt") or 0)

    # If the table collation is case-sensitive, this diagnostic also shows
    # whether LOWER(...) would match. Production currently uses LIKE only.
    lower_cnt = None
    if cnt == 0:
        try:
            cursor.execute(
                f"SELECT COUNT(*) AS cnt FROM {fq} "
                f"WHERE LOWER({quote_ident(build_col)}) LIKE LOWER(%s)",
                (like,),
            )
            lower_cnt = int((cursor.fetchone() or {}).get("cnt") or 0)
        except Exception:
            lower_cnt = None

    devices: List[str] = []
    if device_col and cnt > 0:
        try:
            cursor.execute(
                f"SELECT DISTINCT {quote_ident(device_col)} AS device FROM {fq} "
                f"WHERE {quote_ident(build_col)} LIKE %s "
                f"AND {quote_ident(device_col)} IS NOT NULL "
                f"AND TRIM({quote_ident(device_col)}) <> '' "
                f"ORDER BY {quote_ident(device_col)} LIMIT 20",
                (like,),
            )
            devices = [
                str(r.get("device") or "").strip()
                for r in (cursor.fetchall() or [])
                if str(r.get("device") or "").strip()
            ]
        except Exception as exc:
            devices = [f"[device fetch error: {exc}]"]

    samples: List[Dict[str, Any]] = []
    if cnt > 0 and sample_limit > 0:
        select_parts = [
            f"{quote_ident(build_col)} AS metabuild",
        ]
        if ticket_col:
            select_parts.append(f"{quote_ident(ticket_col)} AS ticket")
        if date_col:
            select_parts.append(f"{quote_ident(date_col)} AS jira_date")
        if device_col:
            select_parts.append(f"{quote_ident(device_col)} AS serial_no")
        if title_col:
            select_parts.append(f"{quote_ident(title_col)} AS jira_title")
        if cr_col:
            select_parts.append(f"{quote_ident(cr_col)} AS mapped_cr")
        order_col = date_col or ticket_col or build_col
        try:
            cursor.execute(
                f"SELECT {', '.join(select_parts)} FROM {fq} "
                f"WHERE {quote_ident(build_col)} LIKE %s "
                f"ORDER BY {quote_ident(order_col)} DESC LIMIT %s",
                (like, int(sample_limit)),
            )
            samples = [
                {k: ser(v) for k, v in (sample or {}).items()}
                for sample in (cursor.fetchall() or [])
            ]
        except Exception as exc:
            samples = [{"sample_error": str(exc)}]

    return {
        "ok": True,
        "count": cnt,
        "lower_count": lower_cnt,
        "min_date": ser(row.get("min_date")),
        "max_date": ser(row.get("max_date")),
        "device_samples": devices,
        "samples": samples,
    }


def inspect_table(
    cursor,
    schema: str,
    table: str,
    build: str,
    sample_limit: int = 3,
) -> Dict[str, Any]:
    columns = table_columns(cursor, schema, table)
    build_col = first_col(columns, BUILD_COL_CANDIDATES)
    date_col = first_col(columns, DATE_COL_CANDIDATES)
    device_col = first_col(columns, DEVICE_COL_CANDIDATES)
    ticket_col = first_col(columns, TICKET_COL_CANDIDATES)
    title_col = first_col(columns, TITLE_COL_CANDIDATES)
    cr_col = first_col(columns, CR_COL_CANDIDATES)

    result: Dict[str, Any] = {
        "schema": schema,
        "table": table,
        "fq_table": quote_fq(schema, table),
        "columns": columns,
        "build_col": build_col,
        "date_col": date_col,
        "device_col": device_col,
        "ticket_col": ticket_col,
        "title_col": title_col,
        "cr_col": cr_col,
    }

    if not build_col:
        result.update({"ok": False, "reason": "no build/metabuild column"})
        return result

    counts = count_build_rows(
        cursor,
        schema=schema,
        table=table,
        build_col=build_col,
        date_col=date_col,
        device_col=device_col,
        ticket_col=ticket_col,
        title_col=title_col,
        cr_col=cr_col,
        build=build,
        sample_limit=sample_limit,
    )
    result.update(counts)
    if not counts.get("ok"):
        result["reason"] = counts.get("error") or "count failed"
    elif int(counts.get("count") or 0) <= 0:
        result["reason"] = "zero rows"
    else:
        result["reason"] = "matched"
    return result


def print_match(prefix: str, info: Dict[str, Any]) -> None:
    print(f"{prefix} {info.get('schema')}.{info.get('table')}")
    print(
        "    columns:"
        f" build={info.get('build_col') or '-'}"
        f" ticket={info.get('ticket_col') or '-'}"
        f" date={info.get('date_col') or '-'}"
        f" device={info.get('device_col') or '-'}"
        f" cr={info.get('cr_col') or '-'}"
    )
    print(
        f"    count={info.get('count', 0)}"
        f" min_date={info.get('min_date') or '-'}"
        f" max_date={info.get('max_date') or '-'}"
    )
    if info.get("lower_count") is not None:
        print(f"    lower_like_count={info.get('lower_count')}  (production currently uses plain LIKE)")
    devices = info.get("device_samples") or []
    if devices:
        print(f"    device samples ({len(devices)}): {', '.join(str(d) for d in devices[:20])}")
    samples = info.get("samples") or []
    if samples:
        print("    sample rows:")
        for sample in samples:
            bits = [f"{k}={v}" for k, v in sample.items()]
            print("      - " + "; ".join(bits))


def run_targets_config_scan(
    cursor,
    build: str,
    target_hint: str,
    sample_limit: int,
    verbose: bool,
) -> List[Dict[str, Any]]:
    print("\n=== TARGETS_CONFIG scan (existing production first pass) ===")
    candidates = target_candidates(target_hint)
    print(f"Configured target candidates: {len(candidates)}")
    if target_hint:
        print(f"Target hint filter: {target_hint}")

    matches: List[Dict[str, Any]] = []
    skipped = Counter()

    for target in candidates:
        for suffix, source in (("jiras", "jira"), ("openjiras", "openjira")):
            schema = target["schema"]
            table = f"{target['prefix']}_{suffix}"

            # Mirror route behavior: prefer central fq_table_for_target if it
            # knows an exact physical table name.
            try:
                maybe_fq = fq_table_for_target(target["target"], suffix)
                maybe_schema, maybe_table = table_name_from_fq(maybe_fq)
                if maybe_schema and maybe_table:
                    schema, table = maybe_schema, maybe_table
            except Exception:
                pass

            label = f"{schema}.{table}"
            if not table_exists(cursor, schema, table):
                skipped["table not found"] += 1
                if verbose:
                    print(f"[SKIP] {label} target={target['target']} reason=table not found")
                continue

            info = inspect_table(cursor, schema, table, build, sample_limit=sample_limit)
            info.update(target)
            info["source"] = source

            if info.get("reason") == "matched":
                print_match("[MATCH]", info)
                matches.append(info)
            else:
                skipped[str(info.get("reason") or "unknown")] += 1
                if verbose:
                    print(
                        f"[SKIP] {label} target={target['target']} "
                        f"reason={info.get('reason')} build_col={info.get('build_col') or '-'} "
                        f"count={info.get('count', 0)}"
                    )

    print("TARGETS_CONFIG skip summary:", dict(skipped))
    print(f"TARGETS_CONFIG matches: {len(matches)}")
    return matches


def find_direct_tables(
    cursor,
    schemas: Sequence[str],
    table_like: str = "",
) -> List[Tuple[str, str]]:
    params: List[Any] = []
    where = ["table_type='BASE TABLE'", "table_name REGEXP '(_jiras|_openjiras)$'"]
    if schemas:
        placeholders = ",".join(["%s"] * len(schemas))
        where.append(f"table_schema IN ({placeholders})")
        params.extend(list(schemas))
    if table_like:
        where.append("table_name LIKE %s")
        params.append(f"%{table_like}%")

    cursor.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY table_schema, table_name",
        tuple(params),
    )
    return [
        (str(r.get("table_schema") or ""), str(r.get("table_name") or ""))
        for r in (cursor.fetchall() or [])
        if r.get("table_schema") and r.get("table_name")
    ]


def infer_target_from_table(table_name: str) -> Tuple[str, str]:
    table = str(table_name or "")
    if table.endswith("_openjiras"):
        return table[: -len("_openjiras")], "openjira"
    if table.endswith("_jiras"):
        return table[: -len("_jiras")], "jira"
    return table, ""


def run_information_schema_scan(
    cursor,
    build: str,
    table_like: str,
    sample_limit: int,
    verbose: bool,
) -> List[Dict[str, Any]]:
    print("\n=== information_schema direct scan (diagnostic fallback candidate) ===")
    schemas = configured_schemas(cursor)
    print(f"Schemas scanned ({len(schemas)}): {', '.join(schemas) if schemas else '(all schemas)'}")
    if table_like:
        print(f"Table name filter: %{table_like}%")

    tables = find_direct_tables(cursor, schemas, table_like=table_like)
    print(f"Candidate *_jiras/*_openjiras tables found: {len(tables)}")

    matches: List[Dict[str, Any]] = []
    skipped = Counter()

    for schema, table in tables:
        info = inspect_table(cursor, schema, table, build, sample_limit=sample_limit)
        target, source = infer_target_from_table(table)
        info.update(
            {
                "target": target,
                "target_display": target,
                "bu": "",
                "source": source,
                "prefix": target,
            }
        )

        if info.get("reason") == "matched":
            print_match("[MATCH]", info)
            matches.append(info)
        else:
            skipped[str(info.get("reason") or "unknown")] += 1
            if verbose:
                print(
                    f"[SKIP] {schema}.{table} reason={info.get('reason')} "
                    f"build_col={info.get('build_col') or '-'} count={info.get('count', 0)}"
                )

    print("information_schema skip summary:", dict(skipped))
    print(f"information_schema matches: {len(matches)}")
    return matches


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose PDT Buddy Build Report DB lookup.")
    parser.add_argument("build", help="Full or partial build / MetaBuild ID to search.")
    parser.add_argument("--target", default="", help="Optional target hint for TARGETS_CONFIG scan.")
    parser.add_argument("--table-like", default="", help="Optional information_schema table name filter, e.g. nord_hgy_adas_5_1_9_0.")
    parser.add_argument("--samples", type=int, default=3, help="Sample matching rows per matched table.")
    parser.add_argument("--verbose", action="store_true", help="Print skipped tables as well as matches.")
    parser.add_argument("--direct-only", action="store_true", help="Skip TARGETS_CONFIG scan.")
    parser.add_argument("--targets-only", action="store_true", help="Skip information_schema direct scan.")
    args = parser.parse_args(argv)

    build = norm_build(args.build)
    if not build:
        print("[FATAL] Build value is blank after normalization.", file=sys.stderr)
        return 2

    print("=== Build Lookup Diagnostic ===")
    print(f"Project root : {ROOT}")
    print(f"Input build  : {args.build}")
    print(f"Search build : {build}")
    print(f"LIKE pattern : %{build}%")
    print("Mode         : read-only SELECT diagnostics")

    conn = get_mysql_connection_db()
    if not conn:
        print("[FATAL] get_mysql_connection_db() returned no connection.", file=sys.stderr)
        return 3

    cursor = conn.cursor(dictionary=True)
    try:
        target_matches: List[Dict[str, Any]] = []
        direct_matches: List[Dict[str, Any]] = []

        if not args.direct_only:
            target_matches = run_targets_config_scan(
                cursor,
                build=build,
                target_hint=args.target,
                sample_limit=max(0, int(args.samples or 0)),
                verbose=bool(args.verbose),
            )

        if not args.targets_only:
            direct_matches = run_information_schema_scan(
                cursor,
                build=build,
                table_like=args.table_like,
                sample_limit=max(0, int(args.samples or 0)),
                verbose=bool(args.verbose),
            )

        print("\n=== SUMMARY ===")
        print(f"TARGETS_CONFIG matches      : {len(target_matches)}")
        print(f"information_schema matches  : {len(direct_matches)}")

        all_matches = target_matches + direct_matches
        if not all_matches:
            print("No matching rows were found for the given build string.")
            print("Next checks:")
            print("  1) Try a shorter partial such as -- build number only: 00027")
            print("  2) Try --table-like with the exact table prefix")
            print("  3) Verify the exact DB value with SELECT DISTINCT metabuild ... LIMIT 20")
            return 1

        seen = set()
        print("\nMatched tables:")
        for info in all_matches:
            key = (info.get("schema"), info.get("table"))
            if key in seen:
                continue
            seen.add(key)
            print(
                f"  - {info.get('schema')}.{info.get('table')}"
                f" | target={info.get('target') or '-'}"
                f" | source={info.get('source') or '-'}"
                f" | build_col={info.get('build_col')}"
                f" | rows={info.get('count')}"
                f" | date={info.get('min_date') or '-'}..{info.get('max_date') or '-'}"
            )

        if not target_matches and direct_matches:
            print("\nDIAGNOSIS:")
            print("  The build exists in DB tables found by information_schema,")
            print("  but the existing TARGETS_CONFIG production scan did not find it.")
            print("  This usually means dashboard_status target/db_prefix metadata is missing or mismatched.")
            print("  A production fallback scan can fix this without changing existing registered-target behavior.")

        return 0
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())