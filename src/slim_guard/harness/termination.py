from enum import StrEnum


class HarnessTermination(StrEnum):
    FINAL_RESPONSE = "final_response"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    FATAL_ERROR = "fatal_error"
