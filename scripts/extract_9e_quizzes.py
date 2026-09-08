"""DEPRECATED — utilisait DeepSeek. Utilise gemini_9e_quiz_builder.py."""
import sys

if __name__ == "__main__":
    print(
        "Script désactivé (DeepSeek). Utilise:\n"
        "  set GEMINI_API_KEY=...\n"
        "  python scripts/gemini_9e_quiz_builder.py --all --from-candidates"
    )
    sys.exit(1)
