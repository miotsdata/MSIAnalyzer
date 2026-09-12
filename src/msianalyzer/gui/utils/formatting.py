import datetime


def format_minute_precision(start_date: str) -> str:
    """`start_date` (`str(datetime.datetime)`-shaped, e.g.
    "2026-01-01 12:00:00.123456") truncated to minute precision
    ("2026-01-01 12:00") — seconds/microseconds are noise in a list of
    analyses or an analysis' own header. Falls back to `start_date`
    unchanged if it doesn't parse.
    """
    if not start_date:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(start_date)
    except ValueError:
        return start_date
    return dt.strftime("%Y-%m-%d %H:%M")
