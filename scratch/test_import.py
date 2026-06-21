import sys
try:
    from django.db import models
    print(f"models is: {models}")
    print(f"models.Q is: {models.Q}")
except Exception as e:
    print(f"Error: {e}")
