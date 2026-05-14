# ---------------------------------
# CLEAN DATE (UNIVERSAL FORMAT)
# ---------------------------------

from core.logger import setup_logger


logger = setup_logger("monitor_engine" ,"monitor")


def clean_date(value):
    """
    Clean and normalize ANY date format to YYYY-MM-DD
    Handles: DD-MM-YYYY, DD/MM/YYYY, MM-DD-YYYY, MM/DD/YYYY, YYYY-MM-DD, YYYY/MM/DD
    """
    if value is None:
        return None
    
    try:
        import re
        from datetime import datetime
        
        # Convert to string and clean
        date_str = str(value).strip()
        
        # Remove any time portion if exists
        if 'T' in date_str:
            date_str = date_str.split('T')[0]
        if ' ' in date_str:
            date_str = date_str.split(' ')[0]
        
        # Try to detect and parse different date formats
        # Pattern 1: YYYY-MM-DD or YYYY/MM/DD (Year first)
        match_year_first = re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$', date_str)
        if match_year_first:
            year, month, day = match_year_first.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
        
        # Pattern 2: DD-MM-YYYY or DD/MM/YYYY (Day first)
        match_day_first = re.match(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$', date_str)
        if match_day_first:
            day, month, year = match_day_first.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
        
        # Pattern 3: MM-DD-YYYY or MM/DD/YYYY (Month first)
        match_month_first = re.match(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$', date_str)
        if match_month_first:
            month, day, year = match_month_first.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
        
        # If all else fails, try datetime parser
        try:
            # Try common formats
            for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y', '%m-%d-%Y', '%m/%d/%Y']:
                try:
                    parsed_date = datetime.strptime(date_str, fmt)
                    return parsed_date.strftime('%Y-%m-%d')
                except ValueError:
                    continue
            
            # If datetime parser fails, log warning and return original
            logger.warning(f"UNUSUAL_DATE_FORMAT | {date_str}")
            return date_str
            
        except Exception as e:
            logger.warning(f"DATE_PARSE_ERROR | {date_str} | {e}")
            return date_str
        
    except Exception as e:
        logger.error(f"DATE_CLEAN_ERROR | {e}")
        return None