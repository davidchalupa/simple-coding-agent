from enum import Enum


class TurnStatus(Enum):
    OK = 1
    END_TURN = 2
    TRY_AGAIN = 3
