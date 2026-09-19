"""FIX 4.4 tag numbers and field values, named once so no digit is loose in the code.

Values follow the FIX 4.4 specification. Where FIX has no field for something
this exchange needs, a tag from the 20000 to 39999 range is used: the FIX
Trading Community set that range aside for use between two parties without
registering a number.
"""

import enum
from typing import Final

# Framing.
BEGIN_STRING: Final = 8
BODY_LENGTH: Final = 9
CHECK_SUM: Final = 10

# Header.
MSG_TYPE: Final = 35
SENDER_COMP_ID: Final = 49
TARGET_COMP_ID: Final = 56
MSG_SEQ_NUM: Final = 34
POSS_DUP_FLAG: Final = 43
POSS_RESEND: Final = 97
SENDING_TIME: Final = 52
ORIG_SENDING_TIME: Final = 122

# Session messages.
ENCRYPT_METHOD: Final = 98
HEART_BT_INT: Final = 108
RESET_SEQ_NUM_FLAG: Final = 141
TEST_REQ_ID: Final = 112
BEGIN_SEQ_NO: Final = 7
END_SEQ_NO: Final = 16
NEW_SEQ_NO: Final = 36
GAP_FILL_FLAG: Final = 123
REF_SEQ_NUM: Final = 45
REF_TAG_ID: Final = 371
REF_MSG_TYPE: Final = 372
SESSION_REJECT_REASON: Final = 373
TEXT: Final = 58

# Orders and executions.
ACCOUNT: Final = 1
CL_ORD_ID: Final = 11
ORIG_CL_ORD_ID: Final = 41
ORDER_ID: Final = 37
EXEC_ID: Final = 17
EXEC_TYPE: Final = 150
ORD_STATUS: Final = 39
ORD_TYPE: Final = 40
ORD_REJ_REASON: Final = 103
EXEC_RESTATEMENT_REASON: Final = 378
SIDE: Final = 54
SYMBOL: Final = 55
PRICE: Final = 44
ORDER_QTY: Final = 38
TIME_IN_FORCE: Final = 59
LAST_QTY: Final = 32
LAST_PX: Final = 31
LEAVES_QTY: Final = 151
CUM_QTY: Final = 14
AVG_PX: Final = 6
TRANSACT_TIME: Final = 60
CXL_REJ_RESPONSE_TO: Final = 434
CXL_REJ_REASON: Final = 102

# Defined between the two parties: FIX 4.4 has no field for a self-trade choice.
SELF_TRADE_INSTRUCTION: Final = 20001


class MsgType(enum.StrEnum):
    """The message types this exchange speaks."""

    HEARTBEAT = "0"
    TEST_REQUEST = "1"
    RESEND_REQUEST = "2"
    REJECT = "3"
    SEQUENCE_RESET = "4"
    LOGOUT = "5"
    EXECUTION_REPORT = "8"
    ORDER_CANCEL_REJECT = "9"
    LOGON = "A"
    NEW_ORDER_SINGLE = "D"
    ORDER_CANCEL_REQUEST = "F"
    ORDER_CANCEL_REPLACE_REQUEST = "G"
    BUSINESS_MESSAGE_REJECT = "j"


ADMIN_MESSAGES: Final = frozenset(
    {
        MsgType.HEARTBEAT,
        MsgType.TEST_REQUEST,
        MsgType.RESEND_REQUEST,
        MsgType.REJECT,
        MsgType.SEQUENCE_RESET,
        MsgType.LOGOUT,
        MsgType.LOGON,
    }
)


class ExecType(enum.StrEnum):
    """ExecType (150). FIX 4.3 replaced the separate partial fill and fill values with Trade."""

    NEW = "0"
    CANCELED = "4"
    REPLACED = "5"
    REJECTED = "8"
    RESTATED = "D"
    TRADE = "F"


class OrdStatus(enum.StrEnum):
    """OrdStatus (39): where the order stands now."""

    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    CANCELED = "4"
    REJECTED = "8"


class OrdType(enum.StrEnum):
    """OrdType (40)."""

    MARKET = "1"
    LIMIT = "2"


class TimeInForce(enum.StrEnum):
    """TimeInForce (59). This exchange accepts day and immediate-or-cancel."""

    DAY = "0"
    IMMEDIATE_OR_CANCEL = "3"


class FixSide(enum.StrEnum):
    """Side (54)."""

    BUY = "1"
    SELL = "2"


class OrdRejReason(enum.StrEnum):
    """OrdRejReason (103): why a new order was refused."""

    EXCHANGE_OPTION = "0"
    UNKNOWN_SYMBOL = "1"
    UNSUPPORTED_CHARACTERISTIC = "11"
    INCORRECT_QUANTITY = "13"
    UNKNOWN_ACCOUNT = "15"
    DUPLICATE_ORDER = "6"
    OTHER = "99"


class CxlRejReason(enum.StrEnum):
    """CxlRejReason (102): why a cancel or replace was refused."""

    TOO_LATE_TO_CANCEL = "0"
    UNKNOWN_ORDER = "1"
    DUPLICATE_CL_ORD_ID = "6"
    OTHER = "99"


class CxlRejResponseTo(enum.StrEnum):
    """CxlRejResponseTo (434): which request is being refused."""

    CANCEL = "1"
    REPLACE = "2"


class SessionRejectReason(enum.StrEnum):
    """SessionRejectReason (373): why a message was rejected at the session level."""

    REQUIRED_TAG_MISSING = "1"
    VALUE_IS_INCORRECT = "5"
    INCORRECT_DATA_FORMAT = "6"
    COMP_ID_PROBLEM = "9"
    INVALID_MSG_TYPE = "11"
    OTHER = "99"


class ExecRestatementReason(enum.StrEnum):
    """ExecRestatementReason (378): why the exchange changed or cancelled an order itself."""

    REPRICING_OF_ORDER = "3"
    MARKET_OPTION = "8"


class SelfTradeInstruction(enum.StrEnum):
    """The self-trade choice carried on an order, in the tag agreed between the parties."""

    CANCEL_ACTIVE = "A"
    CANCEL_PASSIVE = "P"
