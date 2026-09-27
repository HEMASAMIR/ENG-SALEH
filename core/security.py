import hmac
import hashlib
from config import settings

def generate_hmac(user_id, audit_timestamp, action_type):
    """
    Generates HMAC as hex digest.
    Combines user_id + audit_timestamp + action_type as string.
    """
    message = f"{user_id}|{audit_timestamp}|{action_type}".encode('utf-8')
    return hmac.new(settings.SECRET_KEY, message, hashlib.sha256).hexdigest()