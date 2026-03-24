"""Deterministic SQL handlers for well-defined CRM task categories.

Instead of letting the LLM write SQL from scratch, these handlers:
1. Extract parameters from the question (time period, threshold, direction)
2. Build a fixed SQL query template
3. Execute it and return the answer directly

The LLM is only used as a fallback when deterministic extraction fails.
"""

import logging
import re
from crm_database import CRMDatabase

logger = logging.getLogger(__name__)


def _extract_time_filter(question: str, ref_date: str) -> str:
    """Extract date filter SQL from question text.

    Returns a SQL WHERE clause fragment using substr(CreatedDate,1,10)
    because timestamps have timezone suffix that breaks date comparisons.
    Returns empty string if no time filter detected.
    """
    C = "substr(CreatedDate,1,10)"  # strip timezone for SQLite date comparison
    q = question.lower()

    word_nums = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12,
    }

    def _rel(offset: str) -> str:
        return f"{C} >= date('{ref_date}', '{offset}')"

    # "past N quarters" / "last N quarters" (digit)
    m = re.search(r"(?:past|last|over the (?:past|last))\s+(\d+)\s+quarter", q)
    if m:
        return _rel(f"-{int(m.group(1)) * 3} months")

    # "past N months" (digit)
    m = re.search(r"(?:past|last|over the (?:past|last))\s+(\d+)\s+month", q)
    if m:
        return _rel(f"-{int(m.group(1))} months")

    # "past N weeks" (digit)
    m = re.search(r"(?:past|last|over the (?:past|last))\s+(\d+)\s+week", q)
    if m:
        return _rel(f"-{int(m.group(1)) * 7} days")

    # "past four/two/three months/quarters/weeks" (word numbers)
    m = re.search(r"(?:past|last|over the (?:past|last))\s+(\w+)\s+(quarter|month|week)", q)
    if m and m.group(1) in word_nums:
        n = word_nums[m.group(1)]
        unit = m.group(2)
        if unit == "quarter":
            return _rel(f"-{n * 3} months")
        elif unit == "month":
            return _rel(f"-{n} months")
        elif unit == "week":
            return _rel(f"-{n * 7} days")

    # Named month + year: "October 2021", "in May 2021"
    m = re.search(
        r"(?:in\s+)?(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})",
        q,
    )
    if m:
        month_map = {
            "january": "01", "february": "02", "march": "03", "april": "04",
            "may": "05", "june": "06", "july": "07", "august": "08",
            "september": "09", "october": "10", "november": "11", "december": "12",
        }
        mn = month_map[m.group(1)]
        yr = m.group(2)
        return f"{C} >= '{yr}-{mn}-01' AND {C} < date('{yr}-{mn}-01', '+1 month')"

    # Season + year: "Spring 2023", "Fall 2022"
    m = re.search(r"(?:in\s+)?(?:the\s+)?(spring|summer|fall|autumn|winter)\s+(?:of\s+)?(\d{4})", q)
    if m:
        season_ranges = {
            "spring": ("03-01", "06-01"),
            "summer": ("06-01", "09-01"),
            "fall": ("09-01", "12-01"),
            "autumn": ("09-01", "12-01"),
            "winter": ("12-01", "03-01"),
        }
        s, e = season_ranges[m.group(1)]
        yr = m.group(2)
        if m.group(1) == "winter":
            return f"({C} >= '{yr}-{s}' OR {C} < '{int(yr)+1}-{e}')"
        return f"{C} >= '{yr}-{s}' AND {C} < '{yr}-{e}'"

    # "Q1/Q2/Q3/Q4 of 2022" or "first/second/third/fourth quarter of 2022"
    m = re.search(r"(?:the\s+)?(?:q([1-4])|(?:(first|second|third|fourth)\s+quarter))\s+(?:of\s+)?(\d{4})", q)
    if m:
        qn = m.group(1) or {"first": "1", "second": "2", "third": "3", "fourth": "4"}[m.group(2)]
        yr = m.group(3)
        q_starts = {"1": "01-01", "2": "04-01", "3": "07-01", "4": "10-01"}
        q_ends = {"1": "04-01", "2": "07-01", "3": "10-01", "4": f"{int(yr)+1}-01-01"}
        qs = q_starts[qn]
        qe = q_ends[qn]
        if qn == "4":
            return f"{C} >= '{yr}-{qs}' AND {C} < '{qe}'"
        return f"{C} >= '{yr}-{qs}' AND {C} < '{yr}-{qe}'"

    return ""


def _extract_threshold(question: str) -> int:
    """Extract 'more than N cases' threshold. Returns 0 if not found."""
    q = question.lower()
    m = re.search(r"more than\s+(\w+)\s+case", q)
    if m:
        word_nums = {
            "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
        }
        val = m.group(1)
        return word_nums.get(val, int(val) if val.isdigit() else 0)
    # "managing cases" / "handling cases" with no number = more than 0
    if re.search(r"(?:managing|handling|dealt with)\s+cases", q):
        return 0
    return 0


def _extract_direction(question: str) -> str:
    """Extract whether we want lowest/highest. Returns 'ASC' or 'DESC'."""
    q = question.lower()
    if any(w in q for w in ["lowest", "least", "smallest", "quickest", "fastest", "shortest"]):
        return "ASC"
    if any(w in q for w in ["highest", "most", "greatest", "top", "largest", "slowest", "longest"]):
        return "DESC"
    return "ASC"


def handle_handle_time(question: str, db: CRMDatabase, ref_date: str) -> str | None:
    """Deterministic handler for handle_time category.

    Policy: exclude transferred cases (>1 Owner Assignment in CaseHistory__c).
    """
    time_filter = _extract_time_filter(question, ref_date)
    threshold = _extract_threshold(question)
    direction = _extract_direction(question)

    where_parts = ['C.ClosedDate IS NOT NULL']
    if time_filter:
        where_parts.append(time_filter.replace(
            "substr(CreatedDate,1,10)", "substr(C.CreatedDate,1,10)"))

    where_clause = " AND ".join(where_parts)

    # Exclude transferred cases: only cases with <= 1 Owner Assignment
    sql = f'''
    SELECT C.OwnerId,
           AVG(julianday(substr(C.ClosedDate,1,19)) - julianday(substr(C.CreatedDate,1,19))) as avg_handle_time,
           COUNT(*) as case_count
    FROM "Case" C
    WHERE {where_clause}
      AND (SELECT COUNT(*) FROM CaseHistory__c CH
           WHERE CH.CaseId__c = C.Id AND CH.Field__c LIKE '%wner%') <= 1
    GROUP BY C.OwnerId
    HAVING COUNT(*) > {threshold}
    ORDER BY avg_handle_time {direction}
    LIMIT 1
    '''

    logger.info(f"handle_time SQL: {sql.strip()}")
    result = db.execute_query(sql)

    if result["success"] and result["data"]:
        owner_id = result["data"][0].get("OwnerId")
        if owner_id:
            return owner_id

    # Fallback: without transfer exclusion
    sql2 = f'''
    SELECT C.OwnerId,
           AVG(julianday(substr(C.ClosedDate,1,19)) - julianday(substr(C.CreatedDate,1,19))) as avg_handle_time,
           COUNT(*) as case_count
    FROM "Case" C
    WHERE C.ClosedDate IS NOT NULL
    GROUP BY C.OwnerId
    HAVING COUNT(*) > {threshold}
    ORDER BY avg_handle_time {direction}
    LIMIT 1
    '''
    result = db.execute_query(sql2)
    if result["success"] and result["data"]:
        return result["data"][0].get("OwnerId")

    return None


def handle_sales_cycle(question: str, db: CRMDatabase, ref_date: str) -> str | None:
    """Deterministic handler for sales_cycle_understanding category."""
    time_filter = _extract_time_filter(question, ref_date)
    direction = _extract_direction(question)

    where_parts = ['CloseDate IS NOT NULL']
    if time_filter:
        where_parts.append(time_filter)

    where_clause = " AND ".join(where_parts)

    sql = f'''
    SELECT OwnerId,
           AVG(julianday(substr(CloseDate,1,19)) - julianday(substr(CreatedDate,1,19))) as avg_cycle,
           COUNT(*) as opp_count
    FROM Opportunity
    WHERE {where_clause}
    GROUP BY OwnerId
    HAVING COUNT(*) > 0
    ORDER BY avg_cycle {direction}
    LIMIT 1
    '''

    logger.info(f"sales_cycle SQL: {sql.strip()}")
    result = db.execute_query(sql)

    if result["success"] and result["data"]:
        owner_id = result["data"][0].get("OwnerId")
        if owner_id:
            return owner_id

    # Fallback without date filter
    if time_filter:
        sql_fallback = f'''
        SELECT OwnerId,
               AVG(julianday(substr(CloseDate,1,19)) - julianday(substr(CreatedDate,1,19))) as avg_cycle,
               COUNT(*) as opp_count
        FROM Opportunity
        WHERE CloseDate IS NOT NULL
        GROUP BY OwnerId
        HAVING COUNT(*) > 0
        ORDER BY avg_cycle {direction}
        LIMIT 1
        '''
        result = db.execute_query(sql_fallback)
        if result["success"] and result["data"]:
            return result["data"][0].get("OwnerId")

    return None


def handle_conversion_rate(question: str, db: CRMDatabase, ref_date: str) -> str | None:
    """Deterministic handler for conversion_rate_comprehension category."""
    time_filter = _extract_time_filter(question, ref_date)
    direction = _extract_direction(question)

    where_parts = []
    if time_filter:
        where_parts.append(time_filter)

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    sql = f'''
    SELECT OwnerId,
           CAST(SUM(CASE WHEN IsConverted = 'true' THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as conv_rate,
           COUNT(*) as lead_count
    FROM Lead
    {where_clause}
    GROUP BY OwnerId
    HAVING COUNT(*) > 0
    ORDER BY conv_rate {direction}
    LIMIT 1
    '''

    logger.info(f"conversion_rate SQL: {sql.strip()}")
    result = db.execute_query(sql)

    if result["success"] and result["data"]:
        owner_id = result["data"][0].get("OwnerId")
        if owner_id:
            return owner_id

    return None


def handle_sales_amount(question: str, db: CRMDatabase, ref_date: str) -> str | None:
    """Deterministic handler for sales_amount_understanding category."""
    time_filter = _extract_time_filter(question, ref_date)
    direction = _extract_direction(question)

    # Sales amount uses Order.EffectiveDate, not CreatedDate
    if time_filter:
        time_filter = time_filter.replace("substr(CreatedDate,1,10)", "substr(O.EffectiveDate,1,10)")

    where_parts = []
    if time_filter:
        where_parts.append(time_filter)

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    sql = f'''
    SELECT O.OwnerId,
           SUM(OI.UnitPrice * OI.Quantity) as total_sales
    FROM "Order" O
    JOIN OrderItem OI ON OI.OrderId = O.Id
    {where_clause}
    GROUP BY O.OwnerId
    ORDER BY total_sales {direction}
    LIMIT 1
    '''

    logger.info(f"sales_amount SQL: {sql.strip()}")
    result = db.execute_query(sql)

    if result["success"] and result["data"]:
        owner_id = result["data"][0].get("OwnerId")
        if owner_id:
            return owner_id

    return None


def handle_best_region(question: str, db: CRMDatabase, ref_date: str) -> str | None:
    """Deterministic handler for best_region_identification category."""
    time_filter = _extract_time_filter(question, ref_date)
    direction = _extract_direction(question)

    where_parts = ['C.ClosedDate IS NOT NULL']
    if time_filter:
        # Prefix with C. for Case table
        where_parts.append(time_filter.replace("substr(CreatedDate,1,10)", "substr(C.CreatedDate,1,10)"))

    where_clause = " AND ".join(where_parts)

    sql = f'''
    SELECT A.ShippingState,
           AVG(julianday(substr(C.ClosedDate,1,19)) - julianday(substr(C.CreatedDate,1,19))) as avg_closure
    FROM "Case" C
    JOIN Account A ON C.AccountId = A.Id
    WHERE {where_clause}
      AND A.ShippingState IS NOT NULL
      AND A.ShippingState != ''
    GROUP BY A.ShippingState
    ORDER BY avg_closure {direction}
    LIMIT 1
    '''

    logger.info(f"best_region SQL: {sql.strip()}")
    result = db.execute_query(sql)

    if result["success"] and result["data"]:
        state = result["data"][0].get("ShippingState")
        if state:
            return state

    return None


def handle_transfer_count(question: str, db: CRMDatabase, ref_date: str) -> str | None:
    """Deterministic handler for transfer_count category."""
    time_filter = _extract_time_filter(question, ref_date)
    threshold = _extract_threshold(question)
    direction = _extract_direction(question)

    # Transfer = OwnerId changes tracked in CaseHistory__c
    where_parts = ["CH.Field__c LIKE '%wner%'"]
    if time_filter:
        where_parts.append(time_filter.replace("substr(CreatedDate,1,10)", "substr(CH.CreatedDate,1,10)"))

    where_clause = " AND ".join(where_parts)

    sql = f'''
    SELECT C.OwnerId,
           COUNT(CH.Id) as transfer_count,
           COUNT(DISTINCT C.Id) as case_count
    FROM CaseHistory__c CH
    JOIN "Case" C ON CH.CaseId__c = C.Id
    WHERE {where_clause}
    GROUP BY C.OwnerId
    HAVING COUNT(DISTINCT C.Id) > {threshold}
    ORDER BY transfer_count {direction}
    LIMIT 1
    '''

    logger.info(f"transfer_count SQL: {sql.strip()}")
    result = db.execute_query(sql)

    if result["success"] and result["data"]:
        owner_id = result["data"][0].get("OwnerId")
        if owner_id:
            return owner_id

    return None


# ── Registry ─────────────────────────────────────────────────────

DETERMINISTIC_HANDLERS = {
    "handle_time": handle_handle_time,
    "sales_cycle_understanding": handle_sales_cycle,
    "conversion_rate_comprehension": handle_conversion_rate,
    "sales_amount_understanding": handle_sales_amount,
    "best_region_identification": handle_best_region,
    "transfer_count": handle_transfer_count,
}


def extract_context_date(context: str) -> str:
    """Extract 'Today's date' from required_context if present."""
    m = re.search(r"[Tt]oday'?s?\s+date:?\s*(\d{4}-\d{2}-\d{2})", context)
    if m:
        return m.group(1)
    return ""


def try_deterministic(category: str, question: str, db: CRMDatabase, ref_date: str, context: str = "") -> str | None:
    """Try deterministic handler for a category. Returns answer or None to fall back to LLM."""
    handler = DETERMINISTIC_HANDLERS.get(category)
    if not handler:
        return None

    # Prefer date from context ("Today's date: YYYY-MM-DD") over DB max date
    ctx_date = extract_context_date(context)
    effective_ref = ctx_date or ref_date

    try:
        answer = handler(question, db, effective_ref)
        if answer:
            logger.info(f"Deterministic handler for {category} returned: {answer} (ref={effective_ref})")
        else:
            logger.info(f"Deterministic handler for {category} returned None — falling back to LLM")
        return answer
    except Exception as e:
        logger.warning(f"Deterministic handler for {category} failed: {e}")
        return None
