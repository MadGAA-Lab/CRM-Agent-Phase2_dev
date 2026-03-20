import argparse
import os
from pathlib import Path

import uvicorn

# Load .env from repo root for local development / debug runs.
# Has no effect in Docker (no .env file present) or when vars are already set.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)  # override=False: real env vars take precedence
except ImportError:
    pass  # python-dotenv not installed — rely on environment variables directly

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

from executor import Executor


def main():
    parser = argparse.ArgumentParser(description="Run the A2A agent.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9009, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, help="URL to advertise in the agent card")
    args = parser.parse_args()

    # Fill in your agent card
    # See: https://a2a-protocol.org/latest/tutorials/python/3-agent-skills-and-card/
    
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

    request_handler = DefaultRequestHandler(
        agent_executor=Executor(),
        task_store=InMemoryTaskStore(),
    )
    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    uvicorn.run(server.build(), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
