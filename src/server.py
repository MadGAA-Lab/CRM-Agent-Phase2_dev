import argparse
from pathlib import Path

import uvicorn

# Load .env from repo root for local development / debug runs.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
except ImportError:
    pass

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

from executor import Executor
from db_builder import get_database_path
from crm_database import CRMDatabase
from time_budget import TimeBudget


def main():
    parser = argparse.ArgumentParser(description="Run the A2A agent.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9009, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, help="URL to advertise in the agent card")
    args = parser.parse_args()

    # ── Load CRM database ─────────────────────────────────────────────
    db_path = get_database_path(org_type="b2b")
    db = CRMDatabase(db_path)

    if db.is_available:
        tables = db.get_tables()
        print(f"CRM Database ready: {len(tables)} tables")
    else:
        print("WARNING: CRM Database not available — agent will use context-only mode")

    # ── Agent card ─────────────────────────────────────────────────────
    skill = AgentSkill(
        id="crm_task_solver",
        name="CRM Task Solver",
        description=(
            "Handles CRM tasks including lead qualification, case routing, "
            "sales analytics, and knowledge QA with schema drift adaptation "
            "and context rot filtering for Entropic CRMArena"
        ),
        tags=["crm", "salesforce", "lead", "case", "opportunity", "analytics"],
        examples=[
            "Which agent handles the most leads?",
            "What is the average handle time for cases?",
            "Which competitors are we at a disadvantage against?",
        ],
    )

    agent_card = AgentCard(
        name="MadGAA CRM Agent",
        description=(
            "CRM agent with runtime schema drift adaptation and context rot "
            "filtering for Entropic CRMArena"
        ),
        url=args.card_url or f"http://{args.host}:{args.port}/",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )

    # ── Time budget ─────────────────────────────────────────────────────
    time_budget = TimeBudget()

    # ── Start server ───────────────────────────────────────────────────
    request_handler = DefaultRequestHandler(
        agent_executor=Executor(db=db, time_budget=time_budget),
        task_store=InMemoryTaskStore(),
    )
    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    uvicorn.run(server.build(), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
