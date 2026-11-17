from groq import Groq
from dotenv import load_dotenv
import os

load_dotenv()

_tokens = {"prompt": 0, "completion": 0}

def _get_clients() -> list:
    """Carga todos los clientes disponibles."""
    clients = []
    i = 1
    """
    Models:
      llama-3.3-70b-versatile  — production, summaries, analysis (default)
      llama-3.1-8b-instant     — fast, navigation decisions only
      meta-llama/llama-4-scout-17b-16e-instruct
    """
    while True:
        key = os.getenv(f"GROQ_API_KEY_{i}")
        if not key:
            break
        clients.append(Groq(api_key=key))
        i += 1
    # Fallback a GROQ_API_KEY si no hay numeradas
    if not clients:
        key = os.getenv("GROQ_API_KEY")
        if key:
            clients.append(Groq(api_key=key))
    return clients

_clients = _get_clients()
_current_client_idx = 0


def ask_groq(
    prompt: str,
    model: str = "llama-3.3-70b-versatile",
    max_tokens: int = 2000,
) -> str:
    global _current_client_idx

    for attempt in range(len(_clients)):
        client = _clients[_current_client_idx]
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=max_tokens,
            )

            _tokens["prompt"]     += response.usage.prompt_tokens
            _tokens["completion"] += response.usage.completion_tokens
            total = _tokens["prompt"] + _tokens["completion"]
            print(
                f"[TOKENS][key{_current_client_idx+1}] "
                f"in={response.usage.prompt_tokens} "
                f"out={response.usage.completion_tokens} | "
                f"total={total}"
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower():
                # Rate limit — rotar a la siguiente key
                _current_client_idx = (_current_client_idx + 1) % len(_clients)
                print(f"[RATE LIMIT] Rotando a key {_current_client_idx + 1}")
            else:
                # Error diferente — no rotar
                return f"[Groq error: {e}]"

    return "[Groq error: todas las keys agotadas]"