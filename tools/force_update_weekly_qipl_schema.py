import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils import get_mysql_connection_db

DB = "pdt_stats_dashboard"
TABLE = "weekly_qipl_data"

ALTERS = [
    "MODIFY COLUMN resolution TEXT NULL",
    "MODIFY COLUMN jira_reporter TEXT NULL",
    "MODIFY COLUMN ticket_status TEXT NULL",
    "MODIFY COLUMN target TEXT NULL",
    "MODIFY COLUMN jira_component TEXT NULL",
    "MODIFY COLUMN pl_id TEXT NULL",
    "MODIFY COLUMN host_name TEXT NULL",
    "MODIFY COLUMN type_of_farm TEXT NULL",
    "MODIFY COLUMN cr_status TEXT NULL",
    "MODIFY COLUMN cr_area TEXT NULL",
    "MODIFY COLUMN cr_current_ticket TEXT NULL",
    "MODIFY COLUMN cr_si TEXT NULL",
    "MODIFY COLUMN meta_build VARCHAR(1024) NULL",
]

VERIFY_COLUMNS = [
    "resolution",
    "jira_reporter",
    "ticket_status",
    "target",
    "jira_component",
    "pl_id",
    "host_name",
    "type_of_farm",
    "cr_status",
    "cr_area",
    "cr_current_ticket",
    "cr_si",
    "meta_build",
]


def main():
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        raise SystemExit("DB connection failed")

    ok = []
    fail = []
    cur = conn.cursor()
    try:
        for alter in ALTERS:
            sql = "ALTER TABLE `{}`.`{}` {}".format(DB, TABLE, alter)
            try:
                cur.execute(sql)
                ok.append(alter)
            except Exception as exc:
                fail.append((alter, str(exc)))
        conn.commit()

        marks = ",".join(["%s"] * len(VERIFY_COLUMNS))
        cur.execute(
            """
            SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA=%s
              AND TABLE_NAME=%s
              AND COLUMN_NAME IN ({})
            ORDER BY FIELD(COLUMN_NAME,{})
            """.format(marks, marks),
            [DB, TABLE] + VERIFY_COLUMNS + VERIFY_COLUMNS,
        )
        columns = cur.fetchall() or []
    finally:
        try:
            cur.close()
        finally:
            conn.close()

    print("UPDATED", len(ok))
    for item in ok:
        print(" OK", item)

    print("FAILED", len(fail))
    for item, err in fail:
        print(" FAIL", item, "=>", err)

    print("CURRENT_SCHEMA")
    for row in columns:
        print(row)


if __name__ == "__main__":
    main()