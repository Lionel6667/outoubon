import os
import sys
from groq import Groq

# Simuler l'environnement Django
os.environ['GROQ_API_KEY'] = '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'

client = Groq()

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
