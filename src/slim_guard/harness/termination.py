from enum import StrEnum


class HarnessTermination(StrEnum):
    FINAL_RESPONSE = "final_response"
    WAITING_USER_CONFIRMATION = "waiting_user_confirmation"
    WAITING_HUMAN_REVIEW = "waiting_human_review"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    FATAL_ERROR = "fatal_error"
