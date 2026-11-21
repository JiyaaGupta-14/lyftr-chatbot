import os
from dotenv import load_dotenv
import litellm

load_dotenv("../.env")
model = os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash")
keys = [k.strip() for k in os.getenv("LLM_API_KEYS", "").split(",") if k.strip()]
print("model:", model, "| keys found:", len(keys))

for i, key in enumerate(keys, 1):
    try:
        r = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "Say hi"}],
            api_key=key,
        )
        print(f"key_{i} OK ->", r.choices[0].message.content.strip(),
              "| tokens:", r.usage.prompt_tokens, r.usage.completion_tokens)
    except Exception as e:
        print(f"key_{i} FAILED ->", type(e).__name__, str(e)[:200])