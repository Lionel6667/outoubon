import os
import sys
import django

# Add project root to sys.path
sys.path.append(r'c:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django')

# Setup django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.auth.models import User
from core.views import api_group_chat_history

# Create a mock request
factory = RequestFactory()
request = factory.get('/dashboard/api/group-chat/history/')

# Get a user to authenticate the request
user = User.objects.first()
if not user:
    print("No user found in the database. Please run migrations/seeders first.")
    sys.exit(1)

request.user = user

print(f"Testing api_group_chat_history with user: {user.username}")
try:
    response = api_group_chat_history(request)
    print("SUCCESS!")
    print(f"Status code: {response.status_code}")
    print(response.content.decode('utf-8')[:500])
except Exception as e:
    import traceback
    print("FAILED!")
    traceback.print_exc()
