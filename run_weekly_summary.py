import argparse
from datetime import datetime

from weekly_summary_service import current_monday_sunday, write_all_weekly_summaries, write_target_weekly_summary


def _parse_date(value):
    if not value:
        return None
    return datetime.strptime(value, '%Y-%m-%d').date()


def main():
    parser = argparse.ArgumentParser(description='Generate weekly CR summary JSON under managed_excel/<BU>/<TARGET>.')
    parser.add_argument('--target', help='Generate only one target, e.g. ALDABRA')
    parser.add_argument('--week-start', help='YYYY-MM-DD Monday date. Defaults to current week Monday.')
    parser.add_argument('--week-end', help='YYYY-MM-DD Sunday date. Defaults to current week Sunday.')
    args = parser.parse_args()

    week_start = _parse_date(args.week_start)
    week_end = _parse_date(args.week_end)
    if not week_start or not week_end:
        week_start, week_end = current_monday_sunday()

    if args.target:
        path = write_target_weekly_summary(args.target, week_start, week_end)
        print(path)
    else:
        for item in write_all_weekly_summaries(week_start, week_end):
            print(item)


if __name__ == '__main__':
    main()
