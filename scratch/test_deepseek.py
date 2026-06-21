import django, os, traceback
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
django.setup()

from core import gemini

print("MODEL:", gemini.MODEL)
print("FAST_MODEL:", gemini.FAST_MODEL)
print("API KEY set:", bool(gemini.settings.DEEPSEEK_API_KEY))

# Test direct _call_fast
print("\n--- Test _call_fast ---")
try:
    r = gemini._call_fast("Dis juste 'bonjour' en une phrase.", max_tokens=50)
    print("OK:", r)
except Exception as e:
    traceback.print_exc()

# Test get_chat_response
print("\n--- Test get_chat_response ---")
try:
    r = gemini.get_chat_response('salut', [], 'maths', '', None, None, '', '')
    print("OK:", r[:300])
except Exception as e:
    traceback.print_exc()
