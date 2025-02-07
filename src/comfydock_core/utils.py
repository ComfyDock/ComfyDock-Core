# utils.py

import random
import string

def generate_id(length=8):
    """
    Generate a random ID of a given length.
    """
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))