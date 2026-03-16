Ce qui a été fait
1. agent.py — Boucle agent ReAct

Pruning du contexte : compaction auto quand l'historique dépasse 40 messages (garde les 10 récents + résumé des anciens)
Reflection loop : après 3 erreurs consécutives, injecte un message forçant l'agent à changer d'approche
Retry API : retry automatique sur erreurs réseau/timeout avec 2s de backoff
Feedback intermédiaire : notifie l'utilisateur toutes les 3 itérations
System prompt amélioré : méthodologie PLANIFIER → AGIR → OBSERVER → ADAPTER → RAPPORTER
2. executor.py — Exécution

Timeouts adaptatifs : pip install → 120s, downloads → 180s, git clone → 120s (au lieu de 30s pour tout)
search_web fixé : fallback DuckDuckGo HTML → Lite, URL encoding propre, meilleur parsing
Messages d'erreur enrichis : inclut commande + stderr + stdout pour aider l'agent à comprendre
Validation des paramètres : messages clairs si un paramètre manque
3. telegram_bot.py — Interface

Scheduler branché : démarre automatiquement au boot du bot, notifie via Telegram
Commande /tasks : voir les tâches planifiées
Progress callback : feedback intermédiaire pendant les tâches longues
Notification au démarrage : message Telegram quand le bot est en ligne
4. Auto-start Windows

scripts/autostart.bat — Script pour le Task Scheduler
scripts/install_autostart.ps1 — Installation one-click :

powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
Pour lancer

cd h:/0perso/clawbot
python -m apps.telegram_bot
Et surtout pas python apps/clawbot/main.py (l'ancien V1 avec les [EXEC] tags).

Sources de la recherche :

Error Recovery in AI Agents
4 Fault Tolerance Patterns for AI Agents
ReAct Loop — AI Agent Course
OpenAI Practical Guide to Building Agents
Kimi K2 Tool-Calling Debugging