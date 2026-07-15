"""Verify the LLM gateway (direct OpenAI / custom base_url / Portkey) is wired up.

    python scripts/smoke_llm.py

Reads credentials from .env — nothing is hard-coded. Makes one tiny chat call and
one embedding call so you can confirm both work before running the full pipeline.
"""

from jobradar.config import settings
from jobradar.services.llm import get_client


def main() -> None:
    if settings.portkey_api_key:
        mode = "Portkey gateway"
    elif settings.openai_base_url:
        mode = f"custom base_url ({settings.openai_base_url})"
    else:
        mode = "direct OpenAI"
    print(f"Gateway mode : {mode}")
    print(f"Chat model   : {settings.scoring_model}")
    print(f"Embed model  : {settings.embedding_model} (dim {settings.embedding_dim})")

    client = get_client()

    chat = client.chat.completions.create(
        model=settings.scoring_model,
        messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        max_tokens=5,
    )
    print("Chat reply   :", chat.choices[0].message.content.strip())

    emb = client.embeddings.create(model=settings.embedding_model, input="hello world")
    got = len(emb.data[0].embedding)
    print(f"Embed dim    : {got}")
    if got != settings.embedding_dim:
        print(
            f"[!] WARNING: embedding dim {got} != EMBEDDING_DIM {settings.embedding_dim}. "
            "Update EMBEDDING_DIM in .env (and re-run migrations) to match your model."
        )
    print("LLM gateway OK.")


if __name__ == "__main__":
    main()
