from datetime import datetime
import pytz

def format_tenant_date(dt: datetime, date_format: str = "DD/MM/YYYY") -> str:
    """Format a datetime according to tenant-specific preferences."""
    if not dt:
        return ""
    # Map javascript date formats to python strftime
    mapping = {
        "DD/MM/YYYY": "%d/%m/%Y",
        "MM/DD/YYYY": "%m/%d/%Y",
        "YYYY-MM-DD": "%Y-%m-%d",
    }
    fmt = mapping.get(date_format, "%d/%m/%Y")
    return dt.strftime(fmt)

def format_tenant_currency(amount: float, currency: str = "USD") -> str:
    """Format monetary values according to tenant currency."""
    if amount is None:
        return ""
    # East African currencies are formatted without decimal places, USD with 2
    if currency in ("TZS", "KES", "UGX"):
        return f"{currency} {amount:,.0f}"
    return f"{currency} {amount:,.2f}"

def get_tenant_now(timezone_str: str = "UTC") -> datetime:
    """Get the current time in the tenant's timezone."""
    try:
        tz = pytz.timezone(timezone_str)
    except Exception:
        tz = pytz.UTC
    return datetime.now(tz)
