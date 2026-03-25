"""Rule-based privacy guard for PII/confidential information requests.

Zero LLM calls, zero token consumption — pure efficiency gain.
"""


class PrivacyGuard:
    """
    Detects and rejects PII/confidential information requests.

    Uses the task_category field as the primary signal.
    Returns a standardized rejection message.
    """

    PRIVACY_CATEGORIES = frozenset({
        "private_customer_information",
        "confidential_company_knowledge",
        "internal_operation_data",
    })

    REJECTION_MESSAGE = (
        "I'm sorry, but I cannot provide that information. "
        "This request involves private customer data or confidential company knowledge "
        "that I am not authorized to disclose."
    )

    def is_privacy_request(self, task: dict) -> bool:
        """Check if the task category requires privacy rejection."""
        return task.get("task_category", "") in self.PRIVACY_CATEGORIES

    def get_rejection(self) -> str:
        """Return the standardized rejection message."""
        return self.REJECTION_MESSAGE
