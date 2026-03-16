"""
core/agent.py — Boucle agent Kimi K2 avec function calling.

Architecture ReAct : Reason → Act → Observe → boucle.
- Pruning automatique du contexte quand l'historique dépasse le seuil
- Retry intelligent avec reflection loop sur les erreurs
- Callback de progression pour feedback Telegram intermédiaire
- Audit complet de chaque itération
"""

import json
import logging
from typing import Optional, Callable, Awaitable

from openai import AsyncOpenAI

from .tools import TOOLS
from .executor import execute_tool
from .security import track_tokens, get_quota_status, log_audit

logger = logging.getLogger(__name__)

# ─── Seuils de gestion du contexte ────────────────────────────────

MAX_HISTORY_MESSAGES = 40          # Au-delà, on compacte
COMPACT_KEEP_RECENT = 10          # Messages récents à garder intacts
MAX_TOOL_RESULT_CHARS = 3000      # Troncature résultats tools
MAX_CONSECUTIVE_ERRORS = 3        # Erreurs consécutives avant pause réflexion
DEFAULT_MAX_ITERATIONS = 25       # Sécurité anti-boucle (réduit de 50)

# ─── System prompt amélioré ───────────────────────────────────────

SYSTEM_PROMPT = """Tu es Clawbot, un assistant personnel autonome qui contrôle une VM Windows.

TON RÔLE :
- Exécuter des tâches sur la VM de manière autonome et intelligente
- Créer des projets, coder, rechercher, organiser
- Être proactif : si tu vois qu'une étape intermédiaire est nécessaire, fais-la sans demander
- Toujours sauvegarder tes résultats de recherche et rapports via memory_save / report_save

MÉTHODOLOGIE (OBLIGATOIRE pour les tâches complexes) :
1. PLANIFIER : Avant d'agir, décompose la tâche en étapes claires
2. AGIR : Exécute une étape à la fois via les tools
3. OBSERVER : Lis le résultat de chaque action
4. ADAPTER : Si une erreur survient, analyse-la et essaie une approche différente
5. RAPPORTER : En fin de tâche, résume ce qui a été fait

GESTION DES ERREURS :
- Si un tool retourne une erreur, ANALYSE le message d'erreur avant de réessayer
- Ne réessaie JAMAIS la même commande identique — modifie ton approche
- Après 2 échecs sur la même étape, passe à une alternative ou explique le blocage
- Utilise file_read pour vérifier l'état avant de modifier des fichiers
- Utilise file_list pour explorer avant de supposer qu'un chemin existe

CAPACITÉS (via tools) :
- shell_exec : commandes PowerShell sur la VM (timeout configurable)
- file_read/write/list : gestion de fichiers
- screenshot : voir l'écran (analysé par Gemini Vision)
- app_launch : lancer Blender, VS Code, Chrome, Unreal, n8n, Notion
- git_command : opérations git
- search_web : recherche web
- memory_save/recall : mémoire persistante
- report_save : sauvegarder des rapports (consultables par l'utilisateur)

RÈGLES :
- Réponds toujours en français
- Sois concis mais complet
- Pour les opérations longues (pip install, git clone...), utilise un timeout > 60
- Après une recherche web, sauvegarde un résumé dans memory_save(category="research")
- En fin de tâche complexe, crée un rapport avec report_save
- Ne tourne pas en boucle : si quelque chose échoue 2 fois, explique et propose des alternatives

BASE DE TRAVAIL : C:\\Openclaw
"""


class Agent:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.moonshot.ai/v1",
        model: str = "kimi-k2-0905-preview",
    ):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.conversation_history: list[dict] = []
        self._confirm_callback: Optional[Callable[[str], Awaitable[bool]]] = None
        self._progress_callback: Optional[Callable[[str], Awaitable[None]]] = None

    def set_confirm_callback(self, callback: Callable[[str], Awaitable[bool]]):
        self._confirm_callback = callback

    def set_progress_callback(self, callback: Callable[[str], Awaitable[None]]):
        """Callback pour notifier l'utilisateur de la progression."""
        self._progress_callback = callback

    def reset_conversation(self):
        self.conversation_history = []

    async def _notify_progress(self, message: str):
        """Envoie un message de progression si le callback est défini."""
        if self._progress_callback:
            try:
                await self._progress_callback(message)
            except Exception:
                pass  # Ne pas bloquer l'agent si la notif échoue

    def _compact_history(self):
        """
        Compacte l'historique quand il dépasse MAX_HISTORY_MESSAGES.
        Garde les COMPACT_KEEP_RECENT derniers messages intacts,
        résume les anciens en un seul message système.
        """
        if len(self.conversation_history) <= MAX_HISTORY_MESSAGES:
            return

        old_messages = self.conversation_history[:-COMPACT_KEEP_RECENT]
        recent_messages = self.conversation_history[-COMPACT_KEEP_RECENT:]

        # Construire un résumé des anciens messages
        summary_parts = []
        for msg in old_messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if role == "user":
                summary_parts.append(f"[USER] {content[:200]}")
            elif role == "assistant" and content:
                summary_parts.append(f"[ASSISTANT] {content[:200]}")
            elif role == "tool":
                summary_parts.append(f"[TOOL RESULT] {content[:100]}")

        summary = "\n".join(summary_parts[-15:])  # Garder les 15 dernières entrées du résumé

        # Remplacer l'historique par le résumé + messages récents
        self.conversation_history = [
            {
                "role": "user",
                "content": f"[CONTEXTE PRÉCÉDENT — résumé automatique]\n{summary}\n[FIN DU CONTEXTE]",
            }
        ] + recent_messages

        logger.info(
            f"Historique compacté : {len(old_messages)} anciens → résumé, "
            f"{len(recent_messages)} récents conservés"
        )

    async def run(self, user_message: str, max_iterations: int = DEFAULT_MAX_ITERATIONS) -> str:
        """
        Exécute la boucle agent ReAct pour un message utilisateur.
        Retourne la réponse finale de l'agent (texte).
        """
        # Check quota
        quota = get_quota_status()
        if quota["remaining"] <= 0:
            return f"⚠️ Quota journalier atteint ({quota['used']}/{quota['limit']} tokens). Réessaie demain."

        # Compacter l'historique si nécessaire
        self._compact_history()

        # Ajouter le message utilisateur
        self.conversation_history.append({
            "role": "user",
            "content": user_message,
        })

        iteration = 0
        total_tokens = 0
        consecutive_errors = 0

        while iteration < max_iterations:
            iteration += 1

            # Feedback intermédiaire toutes les 3 itérations
            if iteration > 1 and iteration % 3 == 0:
                await self._notify_progress(
                    f"⏳ Étape {iteration}/{max_iterations} en cours..."
                )

            # Construire les messages pour l'API
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.conversation_history

            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                )
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Erreur API Kimi : {error_msg}")
                log_audit("API_ERROR", error_msg)

                # Retry une fois sur erreur réseau/timeout
                if iteration == 1 or "timeout" in error_msg.lower() or "connection" in error_msg.lower():
                    import asyncio
                    await asyncio.sleep(2)
                    try:
                        response = await self.client.chat.completions.create(
                            model=self.model,
                            messages=messages,
                            tools=TOOLS,
                            tool_choice="auto",
                        )
                    except Exception as e2:
                        return f"❌ Erreur API Kimi (après retry) : {str(e2)}"
                else:
                    return f"❌ Erreur API Kimi : {error_msg}"

            # Track tokens
            if response.usage:
                total_tokens += response.usage.total_tokens
                quota_check = track_tokens(response.usage.total_tokens)
                if not quota_check["ok"]:
                    return f"⚠️ {quota_check['reason']}\n\nDernière réponse partielle de l'agent disponible dans les logs."

            choice = response.choices[0]
            message = choice.message

            # Ajouter la réponse de l'assistant à l'historique
            assistant_msg = {"role": "assistant", "content": message.content}
            if message.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in message.tool_calls
                ]
            self.conversation_history.append(assistant_msg)

            # Si pas de tool calls → réponse finale
            if not message.tool_calls:
                final = message.content or "(pas de réponse)"
                log_audit("AGENT_DONE", f"iterations={iteration}, tokens={total_tokens}")
                return final

            # Exécuter chaque tool call
            has_error_this_round = False

            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}
                    log_audit("JSON_PARSE_ERROR", f"tool={fn_name}, raw={tool_call.function.arguments[:200]}")

                logger.info(f"Tool call [{iteration}]: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:100]})")

                # Exécuter le tool
                result = await execute_tool(fn_name, fn_args, self._confirm_callback)

                # Détecter les erreurs dans le résultat
                is_error = result.startswith("❌") or result.startswith("⛔") or result.startswith("⏰")
                if is_error:
                    has_error_this_round = True

                # Tronquer les résultats trop longs
                if len(result) > MAX_TOOL_RESULT_CHARS:
                    result = result[:MAX_TOOL_RESULT_CHARS] + "\n... (tronqué)"

                # Ajouter le résultat dans l'historique
                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

            # Gestion des erreurs consécutives
            if has_error_this_round:
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    # Injecter un message de réflexion pour forcer l'agent à changer d'approche
                    self.conversation_history.append({
                        "role": "user",
                        "content": (
                            f"[SYSTÈME] ⚠️ {consecutive_errors} erreurs consécutives détectées. "
                            "STOP — Ne réessaie PAS la même approche. "
                            "Analyse les erreurs ci-dessus, explique le problème à l'utilisateur, "
                            "et propose une alternative concrète ou demande des précisions."
                        ),
                    })
                    consecutive_errors = 0  # Reset pour laisser une chance à la nouvelle approche
            else:
                consecutive_errors = 0

            # Compacter si l'historique a trop grossi pendant la boucle
            if len(self.conversation_history) > MAX_HISTORY_MESSAGES + 10:
                self._compact_history()

        # Max iterations atteint
        log_audit("MAX_ITERATIONS", f"iterations={max_iterations}, tokens={total_tokens}")
        return (
            f"⚠️ L'agent a atteint {max_iterations} itérations sans terminer.\n"
            "La tâche était peut-être trop complexe. Essaie de la découper en sous-tâches."
        )


# ─── Factory ──────────────────────────────────────────────────────

def create_agent(api_key: str, **kwargs) -> Agent:
    """Crée une instance d'agent avec les paramètres par défaut."""
    return Agent(api_key=api_key, **kwargs)
