"""Cliente LLM para el race engineer.

Wrapper sobre la API OpenAI-compatible (Qwen en RunPod, o cualquier otro).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from openai import OpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Eres el ingeniero de pista de un piloto en RaceRoom Racing Experience.
Tu trabajo es ayudarle con la estrategia durante la carrera.

Tu enfoque:
- **Neumáticos**: cuándo pararle a cambiar, qué rueda sufre más, si se acerca el cliff de grip.
- **Combustible**: si llega al final, cuánto repostar si para, si puede ahorrar levantando.
- **Estrategia de pit**: cuándo entrar a boxes para salir en aire limpio, undercut/overcut.
- **Rivales**: cómo van los coches que compiten directamente con él, quién ha parado, quién tira más.

Estilo de comunicación:
- Conciso y directo, como un ingeniero real por radio.
- Datos concretos: "te quedan 4 vueltas de grip en el trasero izquierdo", no "los neumáticos se degradan".
- Si no tienes datos suficientes, dilo claro en vez de inventar.
- Usa formato de radio: frases cortas, sin florituras.
- Responde en español.
"""


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    max_tokens: int = 1024
    temperature: float = 0.4


def load_config_from_yaml(path: str) -> LLMConfig:
    """Carga config LLM desde un YAML."""
    import yaml

    with open(path) as f:
        cfg = yaml.safe_load(f)

    llm = cfg.get("llm", {})
    api_key = os.environ.get("RACE_ENGINEER_API_KEY", llm.get("api_key", ""))

    return LLMConfig(
        base_url=llm["base_url"],
        model=llm["model"],
        api_key=api_key,
        max_tokens=llm.get("max_tokens", 1024),
        temperature=llm.get("temperature", 0.4),
    )


class RaceEngineerLLM:
    """Cliente LLM para consultas del ingeniero de pista."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key)
        self._history: list[dict[str, str]] = []

    def ask(self, user_message: str, context: str = "") -> str:
        """Envía una pregunta al LLM con contexto de telemetría.

        Args:
            user_message: Pregunta del piloto.
            context: Datos de telemetría pre-formateados (JSON de los tools).

        Returns:
            Respuesta del ingeniero.
        """
        messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

        if self._history:
            messages.extend(self._history[-6:])

        content = user_message
        if context:
            content = f"[Datos de telemetría actuales]\n{context}\n\n[Pregunta del piloto]\n{user_message}"

        messages.append({"role": "user", "content": content})

        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
            )
            reply = response.choices[0].message.content or ""

            self._history.append({"role": "user", "content": user_message})
            self._history.append({"role": "assistant", "content": reply})

            return reply

        except Exception:
            logger.exception("Error llamando al LLM")
            return "Error de comunicación con el LLM. Revisa la conexión."

    def clear_history(self) -> None:
        self._history.clear()
