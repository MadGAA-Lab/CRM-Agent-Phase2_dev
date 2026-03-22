from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    Task,
    TaskState,
    UnsupportedOperationError,
    InvalidRequestError,
)
from a2a.utils.errors import ServerError
from a2a.utils import (
    new_agent_text_message,
    new_task,
)

import asyncio

from agent import Agent
from crm_database import CRMDatabase
from time_budget import TimeBudget


TERMINAL_STATES = {
    TaskState.completed,
    TaskState.canceled,
    TaskState.failed,
    TaskState.rejected
}


class Executor(AgentExecutor):
    def __init__(self, db: CRMDatabase | None = None, time_budget: TimeBudget | None = None):
        self.db = db
        self.time_budget = time_budget
        self.agents: dict[str, Agent] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        msg = context.message
        if not msg:
            raise ServerError(error=InvalidRequestError(message="Missing message in request"))

        task = context.current_task
        if task and task.status.state in TERMINAL_STATES:
            raise ServerError(error=InvalidRequestError(message=f"Task {task.id} already processed (state: {task.status.state})"))

        if not task:
            task = new_task(msg)
            await event_queue.enqueue_event(task)

        context_id = task.context_id
        agent = self.agents.get(context_id)
        if not agent:
            agent = Agent(db=self.db, time_budget=self.time_budget)
            self.agents[context_id] = agent

        updater = TaskUpdater(event_queue, task.id, context_id)

        timeout_sec = self.time_budget.start_task() if self.time_budget else None

        await updater.start_work()
        try:
            if timeout_sec is not None:
                async with asyncio.timeout(timeout_sec):
                    await agent.run(msg, updater)
            else:
                await agent.run(msg, updater)
            if not updater._terminal_state_reached:
                await updater.complete()
        except TimeoutError:
            print(f"Task timed out after {timeout_sec:.1f}s")
            await agent.handle_timeout(msg, updater)
            if not updater._terminal_state_reached:
                await updater.complete()
        except Exception as e:
            print(f"Task failed with agent error: {e}")
            await updater.failed(new_agent_text_message(f"Agent error: {e}", context_id=context_id, task_id=task.id))
        finally:
            if self.time_budget:
                self.time_budget.end_task()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())
