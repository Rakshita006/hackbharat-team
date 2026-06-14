# Shared slowapi Limiter instance using remote IP to prevent API hammering.

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
