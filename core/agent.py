"""
core/agent.py — Boucle agent Kimi K2 avec function calling.

L'agent reçoit un message, appelle Kimi K2 avec les tools disponibles,
exécute les tool calls, renvoie les résultats à Kimi, et boucle
jusqu'à obtenir une réponse finale (pas de tool call).

Sécurité :
- Quota de tokens (journalier)
- Pas de limite dure d'actions (autonomie) mais safety checks sur chaque action
- Audit log de tout
"""

import json
import logging
from typing import Optional, Callable, Awaitable

from openai import AsyncOpenAI

from .tools import TOOLS
from .executor import execute_tool
from .security import track_tokens, get_quota_status, log_audit

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es Clawbot, un assistant personnel TOTALEMENT AUTONOME qui contrôle une VM Windows.

TON RÔLE :
- Exécuter des tâches complexes de bout en bout sans assistance.
- PROACTIVITÉ : Si l'utilisateur donne un objectif flou ("Installe Blender et fais un cube"), décompose-le en étapes (téléchargement, install, script python blender) et exécute tout.
- AUTO-CORRECTION : Si un outil échoue, examine l'erreur et essaie une autre méthode (ex: powershell -> python).
- PLANIFICATION : Utilise `schedule_task` pour les tâches longues ou récurrentes.
- CONSISTANCE (KB) : Utilise la Knowledge Base (`kb_update`, `kb_query`) pour structurer ton savoir par **Tâches Globales** et **Sous-Thèmes**. C'est ta mémoire à long terme pour rester cohérent sur des projets complexes.

CAPACITÉS (via tools) :
- shell_exec : commandes PowerShell sur la VM.
- file_read/write/list : gestion de fichiers.
- screenshot : voir l'écran (analysé par Gemini Vision).
- app_launch : lancer Blender, VS Code, Chrome, Unreal, n8n, Notion.
- git_command : opérations git.
- schedule_task / task_list : programmer des actions dans le futur.
- kb_update / kb_query : gérer ton savoir structuré (Tâches > Thèmes).
- search_web : recherche web.
- memory_save/recall : mémoire temporaire/rapide.
- report_save : sauvegarder des rapports.

CONSIGNES DE TRAVAIL :
1. ANALYSE : Avant d'agir, réfléchis à haute voix au plan. Consulte la KB (`kb_query`) pour voir si tu as déjà des infos sur ce sujet.
2. ACTION : Enchaîne les tool calls. Après chaque étape importante, mets à jour la KB (`kb_update`).
3. VÉRIFICATION : Après une action UI, fais un `screenshot` pour vérifier le résultat.
4. FINALISATION : Envoie un résumé clair à l'utilisateur et sauvegarde un rapport.

BASE DE TRAVAIL : C:\\Openclaw
RÉPONSES : Toujours en français, ton amical et professionnel.
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

    def set_confirm_callback(self, callback: Callable[[str], Awaitable[bool]]):
        """
        Définit le callback pour les confirmations (depuis Telegram).
        callback(message: str) -> bool (True = confirmé, False = refusé)
        """
        self._confirm_callback = callback

    def reset_conversation(self):
        """Remet à zéro l'historique de conversation."""
        self.conversation_history = []

    async def run(self, user_message: str, max_iterations: int = 50) -> str:
        """
        Exécute la boucle agent pour un message utilisateur.

        Retourne la réponse finale de l'agent (texte).
        max_iterations : sécurité anti-boucle infinie (mais élevé pour l'autonomie).
        """
        # Check quota
        quota = get_quota_status()
        if quota["remaining"] <= 0:
            return f"⚠️ Quota journalier atteint ({quota['used']}/{quota['limit']} tokens). Réessaie demain."

        # Ajouter le message utilisateur
        self.conversation_history.append({
            "role": "user",
            "content": user_message,
        })

        # --- Validation de l'historique (Fix 400 error) ---
        # Si le dernier message était un assistant avec des tool_calls mais sans réponse tool, 
        # l'API Kimi plantera. On nettoie si nécessaire.
        valid_history = []
        last_assistant_with_tools = None
        
        for msg in self.conversation_history:
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                last_assistant_with_tools = msg
                valid_history.append(msg)
            elif role == "tool":
                # Si on a un message tool, il répond au dernier assistant avec tools
                last_assistant_with_tools = None
                valid_history.append(msg)
            elif role == "user":
                # Si on rencontre un nouveau message user ALORS qu'un assistant attendait des outils,
                # on doit supprimer cet assistant pour rester valide.
                if last_assistant_with_tools:
                    logger.warning(f"Nettoyage d'un assistant message d'outil orphelin (ID: {last_assistant_with_tools.get('tool_calls')[0].get('id')})")
                    valid_history.remove(last_assistant_with_tools)
                    last_assistant_with_tools = None
                valid_history.append(msg)
            else:
                valid_history.append(msg)
        
        self.conversation_history = valid_history

        iteration = 0
        total_tokens = 0

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"Agent iteration {iteration}/{max_iterations}")

            # Appel Kimi K2 avec retries (Self-Healing V5)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.conversation_history
            
            response = None
            max_retries = 3
            for attempt in range(max_retries + 1):
                try:
                    response = await self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=TOOLS,
                        tool_choice="auto",
                        timeout=90.0, # Augmenté un peu
                    )
                    break # Succès
                except Exception as e:
                    if attempt < max_retries:
                        wait_time = 2 ** attempt
                        logger.warning(f"⚠️ Erreur API Kimi (tentative {attempt+1}/{max_retries}): {e}. Nouvel essai dans {wait_time}s...")
                        log_audit("API_RETRY", f"attempt={attempt+1}, error={str(e)[:100]}")
                        await asyncio.sleep(wait_time)
                    else:
                        error_msg = f"❌ Erreur API Kimi persistante après {max_retries} essais : {str(e)}"
                        logger.error(error_msg)
                        log_audit("API_ERROR", str(e))
                        return error_msg

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
            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                logger.info(f"Tool call: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:100]})")

                # Exécuter le tool
                result = await execute_tool(fn_name, fn_args, self._confirm_callback)

                # Tronquer les résultats trop longs pour ne pas exploser le contexte
                if len(result) > 3000:
                    result = result[:3000] + "\n... (tronqué)"

                # Ajouter le résultat dans l'historique
                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

        # Si on arrive ici, max_iterations atteint
        return f"⚠️ L'agent a atteint {max_iterations} itérations. Dernière action enregistrée dans les logs."


# ─── Factory ──────────────────────────────────────────────────────

def create_agent(api_key: str, **kwargs) -> Agent:
    """Crée une instance d'agent avec les paramètres par défaut."""
    return Agent(api_key=api_key, **kwargs)
