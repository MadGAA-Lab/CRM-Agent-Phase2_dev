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
        return f"{C} >= date('{ref_date}', '{offset}') AND {C} <= '{ref_date}'"

    # "past N quarters" / "last N quarters" (digit) — align to quarter boundaries
    m = re.search(r"(?:past|last|over the (?:past|last))\s+(\d+)\s+quarter", q)
    if m:
        n = int(m.group(1))
        # Align to quarter start: Q1=01, Q2=04, Q3=07, Q4=10
        month = int(ref_date[5:7])
        q_start_month = ((month - 1) // 3) * 3 + 1
        yr = int(ref_date[:4])
        # Go back N quarters from current quarter start
        total_months_back = n * 3
        start_month = q_start_month - total_months_back
        start_year = yr
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        start_date = f"{start_year}-{start_month:02d}-01"
        return f"{C} >= '{start_date}' AND {C} <= '{ref_date}'"

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
            month = int(ref_date[5:7])
            q_start_month = ((month - 1) // 3) * 3 + 1
            yr = int(ref_date[:4])
            total_months_back = n * 3
            start_month = q_start_month - total_months_back
            start_year = yr
            while start_month <= 0:
                start_month += 12
                start_year -= 1
            start_date = f"{start_year}-{start_month:02d}-01"
            return f"{C} >= '{start_date}' AND {C} <= '{ref_date}'"
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

    Policy from context:
    - Handle time = ClosedDate - CreatedDate (only non-transferred, closed cases)
    - 'managed cases' count = cases currently owned + cases transferred out
    - Transferred case = has >1 Owner Assignment in CaseHistory__c
    - Only compute handle time for NON-transferred closed cases
    """
    time_filter = _extract_time_filter(question, ref_date)
    threshold = _extract_threshold(question)
    direction = _extract_direction(question)

    date_start_cond = ""
    if time_filter:
        date_start_cond = "AND " + time_filter.replace(
            "substr(CreatedDate,1,10)", "substr(C.CreatedDate,1,10)")

    # Policy: managed_count = cases owned + cases transferred out FROM agent
    # Handle time = only non-transferred closed cases currently owned by agent
    sql = f'''
    WITH cases_in_range AS (
        SELECT C.Id, C.OwnerId, C.ClosedDate, C.CreatedDate,
               (SELECT COUNT(*) FROM CaseHistory__c CH
                WHERE CH.CaseId__c = C.Id AND CH.Field__c LIKE '%wner%') as owner_changes
        FROM "Case" C
        WHERE 1=1 {date_start_cond}
    ),
    owned_count AS (
        SELECT OwnerId as agent, COUNT(*) as cnt FROM cases_in_range GROUP BY OwnerId
    ),
    transferred_out_count AS (
        SELECT CH.OldValue__c as agent, COUNT(DISTINCT CH.CaseId__c) as cnt
        FROM CaseHistory__c CH
        JOIN cases_in_range CI ON CH.CaseId__c = CI.Id
        WHERE CH.Field__c LIKE '%wner%' AND CH.OldValue__c != '' AND CH.OldValue__c != CI.OwnerId
        GROUP BY CH.OldValue__c
    ),
    managed_total AS (
        SELECT agent, SUM(cnt) as total FROM (
            SELECT agent, cnt FROM owned_count
            UNION ALL
            SELECT agent, cnt FROM transferred_out_count
        ) GROUP BY agent
    ),
    handle_times AS (
        SELECT OwnerId as agent,
               AVG(julianday(substr(ClosedDate,1,19)) - julianday(substr(CreatedDate,1,19))) as avg_ht
        FROM cases_in_range
        WHERE ClosedDate IS NOT NULL AND owner_changes <= 1
        GROUP BY OwnerId
    )
    SELECT mt.agent as OwnerId, ht.avg_ht, mt.total
    FROM managed_total mt
    JOIN handle_times ht ON ht.agent = mt.agent
    WHERE mt.total > {threshold}
    ORDER BY ht.avg_ht {direction}
    LIMIT 1
    '''

    logger.info(f"handle_time SQL: {sql.strip()}")
    result = db.execute_query(sql)

    if result["success"] and result["data"]:
        owner_id = result["data"][0].get("OwnerId")
        if owner_id:
            return owner_id

    # Fallback: simple query without transfer logic
    sql2 = f'''
    SELECT OwnerId,
           AVG(julianday(substr(ClosedDate,1,19)) - julianday(substr(CreatedDate,1,19))) as avg_ht,
           COUNT(*) as cnt
    FROM "Case"
    WHERE ClosedDate IS NOT NULL
    GROUP BY OwnerId
    HAVING COUNT(*) > {threshold}
    ORDER BY avg_ht {direction}
    LIMIT 1
    '''
    result = db.execute_query(sql2)
    if result["success"] and result["data"]:
        return result["data"][0].get("OwnerId")

    return None


def handle_sales_cycle(question: str, db: CRMDatabase, ref_date: str) -> str | None:
    """Deterministic handler for sales_cycle_understanding category.

    Policy: sales cycle = days between Opportunity.CreatedDate and Contract.CompanySignedDate
    Join: Opportunity.ContractID__c = Contract.Id
    """
    time_filter = _extract_time_filter(question, ref_date)
    direction = _extract_direction(question)

    where_parts = ['CT.CompanySignedDate IS NOT NULL']
    if time_filter:
        where_parts.append(time_filter.replace(
            "substr(CreatedDate,1,10)", "substr(O.CreatedDate,1,10)"))

    where_clause = " AND ".join(where_parts)

    sql = f'''
    SELECT O.OwnerId,
           AVG(julianday(COALESCE(CT.CompanySignedDate, O.CloseDate)) - julianday(substr(O.CreatedDate,1,19))) as avg_cycle,
           COUNT(*) as opp_count
    FROM Opportunity O
    LEFT JOIN Contract CT ON O.ContractID__c = CT.Id
    WHERE {where_clause}
      AND COALESCE(CT.CompanySignedDate, O.CloseDate) IS NOT NULL
    GROUP BY O.OwnerId
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

    return None


def handle_conversion_rate(question: str, db: CRMDatabase, ref_date: str) -> str | None:
    """Deterministic handler for conversion_rate_comprehension category.

    Policy: conversion = leads converted within the time window (ConvertedDate <= ref_date).
    """
    time_filter = _extract_time_filter(question, ref_date)
    direction = _extract_direction(question)

    where_parts = []
    if time_filter:
        where_parts.append(time_filter)

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    sql = f'''
    SELECT OwnerId,
           CAST(SUM(CASE WHEN IsConverted = 1 AND substr(ConvertedDate,1,10) <= '{ref_date}' THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as conv_rate,
           COUNT(*) as lead_count
    FROM Lead
    {where_clause}
    GROUP BY OwnerId
    HAVING COUNT(*) > 0
    ORDER BY conv_rate {direction}, lead_count DESC
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
    """Deterministic handler for transfer_count category.

    Policy:
    - Transfer from A→B adds to A's transfer count (OldValue__c = A)
    - 'managed cases' = cases owned + cases transferred to agent (same as handle_time)
    """
    time_filter = _extract_time_filter(question, ref_date)
    threshold = _extract_threshold(question)
    direction = _extract_direction(question)

    date_cond = ""
    if time_filter:
        date_cond = "AND " + time_filter.replace(
            "substr(CreatedDate,1,10)", "substr(C.CreatedDate,1,10)")

    sql = f'''
    WITH cases_in_range AS (
        SELECT C.Id, C.OwnerId
        FROM "Case" C
        WHERE 1=1 {date_cond}
    ),
    managed AS (
        SELECT OwnerId as agent_id, Id FROM cases_in_range
        UNION
        SELECT CH.OldValue__c as agent_id, CI.Id
        FROM cases_in_range CI
        JOIN CaseHistory__c CH ON CH.CaseId__c = CI.Id
        WHERE CH.Field__c LIKE '%wner%' AND CH.OldValue__c != '' AND CH.OldValue__c != CI.OwnerId
    ),
    transfer_counts AS (
        SELECT CH.OldValue__c as agent_id,
               COUNT(*) as transfers
        FROM CaseHistory__c CH
        JOIN cases_in_range CI ON CH.CaseId__c = CI.Id
        WHERE CH.Field__c LIKE '%wner%' AND CH.OldValue__c != ''
        GROUP BY CH.OldValue__c
    ),
    managed_total AS (
        SELECT agent_id, COUNT(DISTINCT Id) as managed_count
        FROM managed GROUP BY agent_id
    )
    SELECT mt.agent_id as OwnerId,
           COALESCE(tc.transfers, 0) as transfer_count,
           mt.managed_count
    FROM managed_total mt
    LEFT JOIN transfer_counts tc ON tc.agent_id = mt.agent_id
    WHERE mt.managed_count > {threshold}
    ORDER BY COALESCE(tc.transfers, 0) {direction}
    LIMIT 1
    '''

    logger.info(f"transfer_count SQL: {sql.strip()}")
    result = db.execute_query(sql)

    if result["success"] and result["data"]:
        owner_id = result["data"][0].get("OwnerId")
        if owner_id:
            return owner_id

    return None


def handle_lead_routing(question: str, db: CRMDatabase, ref_date: str, context: str = "") -> str | None:
    """Deterministic handler for lead_routing category.

    Policy from context:
    1. Territory match: find territory whose description contains the lead's region
    2. Quote success: among agents in that territory, highest accepted quotes
    3. Workload balance: tiebreak by fewest unconverted leads
    """
    # Extract lead region from context
    m = re.search(r"Lead'?s?\s+region:?\s*([A-Z]{2})", context)
    if not m:
        return None
    region = m.group(1)

    # Step 1: Find matching territory
    result = db.execute_query("SELECT Id, Description FROM Territory2")
    if not result["success"]:
        return None

    territory_id = None
    for row in result["data"]:
        if region in row.get("Description", ""):
            territory_id = row["Id"]
            break
    if not territory_id:
        return None

    # Step 2: Get agents in territory
    result = db.execute_query(
        f"SELECT UserId FROM UserTerritory2Association WHERE Territory2Id = '{territory_id}'"
    )
    if not result["success"] or not result["data"]:
        return None
    agent_ids = [row["UserId"] for row in result["data"]]

    # Step 3: Count accepted quotes per agent + open leads
    best_agent = None
    best_quotes = -1
    best_open_leads = float("inf")

    for aid in agent_ids:
        q_result = db.execute_query(f'''
            SELECT COUNT(*) as cnt FROM Quote Q
            JOIN Opportunity O ON Q.OpportunityId = O.Id
            WHERE O.OwnerId = '{aid}' AND Q.Status = 'Accepted'
        ''')
        quotes = q_result["data"][0]["cnt"] if q_result["success"] and q_result["data"] else 0

        l_result = db.execute_query(f'''
            SELECT COUNT(*) as cnt FROM Lead
            WHERE OwnerId = '{aid}' AND IsConverted = 0
        ''')
        open_leads = l_result["data"][0]["cnt"] if l_result["success"] and l_result["data"] else 0

        if quotes > best_quotes or (quotes == best_quotes and open_leads < best_open_leads):
            best_agent = aid
            best_quotes = quotes
            best_open_leads = open_leads

    if best_agent:
        logger.info(f"lead_routing: region={region} territory={territory_id} agent={best_agent} quotes={best_quotes} leads={best_open_leads}")
    return best_agent


# ── Registry ─────────────────────────────────────────────────────

DETERMINISTIC_HANDLERS = {
    "handle_time": handle_handle_time,
    "sales_cycle_understanding": handle_sales_cycle,
    "conversion_rate_comprehension": handle_conversion_rate,
    "sales_amount_understanding": handle_sales_amount,
    "best_region_identification": handle_best_region,
    "transfer_count": handle_transfer_count,
    "lead_routing": handle_lead_routing,
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
        # Some handlers need context (e.g., lead_routing for region)
        import inspect
        if 'context' in inspect.signature(handler).parameters:
            answer = handler(question, db, effective_ref, context=context)
        else:
            answer = handler(question, db, effective_ref)
        if answer:
            logger.info(f"Deterministic handler for {category} returned: {answer} (ref={effective_ref})")
        else:
            logger.info(f"Deterministic handler for {category} returned None — falling back to LLM")
        return answer
    except Exception as e:
        logger.warning(f"Deterministic handler for {category} failed: {e}")
        return None
