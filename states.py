from aiogram.fsm.state import State, StatesGroup


class BuyFlow(StatesGroup):
    choosing_type = State()
    choosing_period = State()
    waiting_name = State()
    waiting_for_config_name = State()


class AdminFlow(StatesGroup):
    broadcast = State()
    search_user = State()
    edit_key_days = State()      # waiting for new days value
    edit_key_name = State()      # waiting for new key name (rename)
    grant_key_days = State()     # waiting for days when granting key to user
    send_personal_msg = State()  # waiting for message text to send to user
    refund_amount = State()      # waiting for refund amount
    refund_reason = State()      # waiting for refund reason
    payment_search = State()     # waiting for user ID to search payments
    promo_code_create = State()  # waiting for promo code
    promo_code_discount = State()  # waiting for discount percent
    promo_code_uses = State()    # waiting for max uses count


class EmailAuth(StatesGroup):
    waiting_email = State()      # waiting for user to enter email
    waiting_code = State()       # waiting for user to enter verification code
