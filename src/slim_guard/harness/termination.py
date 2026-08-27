from enum import StrEnum


class HarnessTermination(StrEnum):
    FINAL_RESPONSE = "final_response"
    WAITING_USER_CONFIRMATION = "waiting_user_confirmation"
    WAITING_HUMAN_REVIEW = "waiting_human_review"
    MAX_MODEL_CALLS = "max_model_calls"
    MAX_TOOL_CALLS = "max_tool_calls"
    MAX_TOTAL_TOKENS = "max_total_tokens"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    FATAL_ERROR = "fatal_error"
