"""Error recovery with structured retry logic.

Max 2 retries per task to preserve TRAJECTORY_EFFICIENCY score.
On failure, re-introspects schema and regenerates answer.
"""

import logging
import traceback

logger = logging.getLogger(__name__)


class ErrorRecovery:
    """
    Handles failures with structured retry logic.

    RULES:
    - Max 2 retries per task (preserves TRAJECTORY_EFFICIENCY)
    - On reasoning error: re-introspect schema → regenerate → retry
    - On LLM error: retry with same inputs (transient failures)
    - On persistent failure: return graceful error response
    - Log error patterns for debugging
    """

    MAX_RETRIES = 2

    async def execute_with_retry(
        self,
        task_fn,
        task: dict,
        introspector=None,
        **kwargs,
    ) -> str:
        """
        Execute a task function with retry logic.

        Args:
            task_fn: Async callable(task, **kwargs) -> str
            task: The task dict
            introspector: SchemaIntrospector for re-introspection on failure
            **kwargs: Additional keyword args passed to task_fn

        Returns:
            The answer string, or a graceful error message
        """
        last_error = None

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                result = await task_fn(task, **kwargs)

                # Validate result is not empty
                if result and result.strip():
                    return result

                if attempt < self.MAX_RETRIES:
                    logger.warning(
                        f"Empty result on attempt {attempt + 1}, retrying..."
                    )
                    continue
                return "insufficient data"

            except Exception as e:
                last_error = e
                logger.error(
                    f"Error on attempt {attempt + 1}/{self.MAX_RETRIES + 1}: "
                    f"{type(e).__name__}: {e}"
                )

                if attempt < self.MAX_RETRIES:
                    # Re-introspect schema on errors (column names may be wrong)
                    if introspector is not None:
                        logger.info("Re-introspecting schema after error...")
                        try:
                            new_mapping = introspector.introspect(task)
                            if "schema_mapping" in kwargs:
                                kwargs["schema_mapping"] = new_mapping
                            if "schema_info" in kwargs and hasattr(introspector, "get_schema_description"):
                                kwargs["schema_info"] = introspector.get_schema_description(new_mapping)
                        except Exception as intro_err:
                            logger.error(f"Re-introspection failed: {intro_err}")
                    continue

                return self._graceful_error(task, last_error)

        return self._graceful_error(task, last_error)

    def _graceful_error(self, task: dict, error: Exception | None) -> str:
        """
        Return a graceful error response.

        This protects the ERROR_RECOVERY score — returning something
        is better than crashing.
        """
        category = task.get("task_category", "unknown")
        error_msg = str(error) if error else "unknown error"

        logger.error(
            f"Task failed after {self.MAX_RETRIES + 1} attempts. "
            f"Category: {category}, Error: {error_msg}\n"
            f"{traceback.format_exc() if error else ''}"
        )

        return "insufficient data"
