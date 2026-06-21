import os
import sys
from openai import OpenAI

# Test DeepSeek API
os.environ['DEEPSEEK_API_KEY'] = 'sk-d27cda0ce1dc46728c3ad5881a739e7b'

client = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url='https://api.deepseek.com')

try:
    models = client.models.list()
    print("Modèles disponibles :")
    for m in models.data:
        print(f"- {m.id}")
    
    # Test simple
    print("\nTest d'appel...")
    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "user",
                "content": "Bonjour, es-tu prêt ?",
            }
        ],
        model="llama-3.3-70b-versatile",
    )
    print(f"Réponse : {chat_completion.choices[0].message.content}")

except Exception as e:
    print(f"ERREUR : {e}")
