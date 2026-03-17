"""
core/onemin_client.py — Client 1min.ai qui imite l'interface AsyncOpenAI.

1min.ai n'a pas de function calling natif.
On simule le tool calling via prompt engineering :
  - Les définitions de tools sont injectées dans le system prompt
  - Le modèle répond en JSON structuré pour appeler des tools
  - On parse la réponse pour extraire les tool calls

L'interface est compatible avec agent.py : client.chat.completions.create()
retourne un objet avec la même structure que la réponse OpenAI.
"""

import json
import re
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


# ─── Dataclasses imitant la réponse OpenAI ────────────────────────

@dataclass
class FunctionCall:
    name: str
    arguments: str  # JSON string


@dataclass
class ToolCall:
    id: str
    type: str  # "function"
    function: FunctionCall


@dataclass
class Message:
    content: Optional[str]
    tool_calls: Optional[list[ToolCall]] = None


@dataclass
class Choice:
    message: Message


@dataclass
class Usage:
    total_tokens: int


@dataclass
class ChatCompletion:
    choices: list[Choice]
    usage: Optional[Usage] = None


# ─── Génération du prompt de tools ────────────────────────────────

def _format_tool_for_prompt(tool: dict) -> str:
    """Convertit une définition de tool OpenAI en description textuelle."""
    fn = tool["function"]
    name = fn["name"]
    desc = fn["description"]
    params = fn.get("parameters", {}).get("properties", {})
    required = fn.get("parameters", {}).get("required", [])

    param_lines = []
    for pname, pinfo in params.items():
        ptype = pinfo.get("type", "string")
        pdesc = pinfo.get("description", "")
        req = " (REQUIRED)" if pname in required else ""
        enum = ""
        if "enum" in pinfo:
            enum = f" [options: {', '.join(pinfo['enum'])}]"
        param_lines.append(f"    - {pname} ({ptype}{req}){enum}: {pdesc}")

    params_text = "\n".join(param_lines) if param_lines else "    (aucun paramètre)"
    return f"  {name}: {desc}\n  Paramètres:\n{params_text}"


def build_tool_prompt_block(tools: list[dict]) -> str:
    """Construit le bloc d'instructions tool calling à injecter dans le system prompt."""
    tool_descriptions = "\n\n".join(_format_tool_for_prompt(t) for t in tools)

    return f"""
===== INSTRUCTIONS TOOL CALLING =====
Tu as accès à des tools. Pour les utiliser, réponds avec UNIQUEMENT un objet JSON (pas d'autre texte) :
{{"tool_calls": [{{"name": "NOM_DU_TOOL", "arguments": {{"param1": "valeur1"}}}}]}}

Tu peux appeler plusieurs tools en une fois en ajoutant plusieurs entrées dans le tableau.

Si tu n'as PAS besoin d'appeler un tool, réponds avec du texte normal.
CRITIQUE : Ne mélange JAMAIS des appels JSON et du texte dans la même réponse. Choisis l'un ou l'autre.

TOOLS DISPONIBLES:

{tool_descriptions}
===== FIN INSTRUCTIONS TOOL CALLING ====="""


# ─── Parsing de la réponse ────────────────────────────────────────

def parse_response(raw_text: str) -> tuple[Optional[str], Optional[list[ToolCall]]]:
    """
    Parse la réponse du modèle.
    Retourne (content, tool_calls) :
      - Si tool calls détectés : (None, [ToolCall, ...])
      - Sinon : (texte, None)
    """
    text = raw_text.strip()

    # Extraire le JSON s'il est dans un bloc markdown ```json ... ```
    md_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if md_match:
        text = md_match.group(1).strip()

    # Tenter le parsing JSON
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if "tool_calls" in data and isinstance(data["tool_calls"], list):
                calls = []
                for tc in data["tool_calls"]:
                    name = tc.get("name", "")
                    args = tc.get("arguments", {})
                    if not name:
                        continue
                    calls.append(ToolCall(
                        id=f"tc_{uuid.uuid4().hex[:8]}",
                        type="function",
                        function=FunctionCall(
                            name=name,
                            arguments=json.dumps(args, ensure_ascii=False),
                        ),
                    ))
                if calls:
                    return None, calls
        except json.JSONDecodeError:
            pass

    # Pas de tool call → texte normal
    return raw_text.strip(), None


# ─── Conversion de l'historique en prompt texte ───────────────────

def _flatten_messages(messages: list[dict], tools: list[dict] | None) -> str:
    """
    Convertit l'historique de messages OpenAI en un seul prompt texte
    pour l'API 1min.ai.
    """
    parts = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        if role == "system":
            # Injecter les tool definitions dans le system prompt
            sys_text = content
            if tools:
                sys_text += "\n" + build_tool_prompt_block(tools)
            parts.append(f"[SYSTEM]\n{sys_text}")

        elif role == "user":
            parts.append(f"[USER]\n{content}")

        elif role == "assistant":
            # Si l'assistant a fait des tool calls, les re-sérialiser
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                tc_data = {
                    "tool_calls": [
                        {
                            "name": tc["function"]["name"],
                            "arguments": json.loads(tc["function"]["arguments"]),
                        }
                        for tc in tool_calls
                    ]
                }
                parts.append(f"[ASSISTANT]\n{json.dumps(tc_data, ensure_ascii=False)}")
            elif content:
                parts.append(f"[ASSISTANT]\n{content}")

        elif role == "tool":
            tool_id = msg.get("tool_call_id", "?")
            parts.append(f"[TOOL_RESULT id={tool_id}]\n{content}")

    return "\n\n".join(parts)


# ─── Client principal ────────────────────────────────────────────

class _Completions:
    """Namespace pour imiter client.chat.completions.create()."""

    def __init__(self, parent: "AsyncOneMinClient"):
        self._parent = parent

    async def create(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> ChatCompletion:
        return await self._parent._call_api(model, messages, tools)


class _Chat:
    """Namespace pour imiter client.chat.completions."""

    def __init__(self, parent: "AsyncOneMinClient"):
        self.completions = _Completions(parent)


class AsyncOneMinClient:
    """
    Client 1min.ai qui expose la même interface que AsyncOpenAI
    pour être utilisé dans agent.py sans modification.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.chat = _Chat(self)
        self._base_url = "https://api.1min.ai/api/chat-with-ai"

    async def _call_api(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
    ) -> ChatCompletion:
        """Appelle l'API 1min.ai et retourne un objet compatible OpenAI."""

        # Construire le prompt texte à partir de l'historique
        prompt = _flatten_messages(messages, tools)

        payload = {
            "type": "UNIFY_CHAT_WITH_AI",
            "model": model,
            "promptObject": {
                "prompt": prompt,
            },
        }

        headers = {
            "API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

        logger.debug(f"1min.ai request: model={model}, prompt_len={len(prompt)}")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._base_url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(
                        f"1min.ai API error {resp.status}: {error_text[:500]}"
                    )

                data = await resp.json()

        # Extraire la réponse
        try:
            result_list = data["aiRecord"]["aiRecordDetail"]["resultObject"]
            raw_text = result_list[0] if result_list else ""
        except (KeyError, IndexError, TypeError) as e:
            raise Exception(f"1min.ai response parsing error: {e}\nRaw: {str(data)[:500]}")

        # Parser la réponse (texte ou tool calls)
        content, tool_calls = parse_response(raw_text)

        # Estimer les tokens (1min.ai ne retourne pas d'usage)
        estimated_tokens = (len(prompt) + len(raw_text)) // 4

        return ChatCompletion(
            choices=[Choice(message=Message(content=content, tool_calls=tool_calls))],
            usage=Usage(total_tokens=estimated_tokens),
        )
