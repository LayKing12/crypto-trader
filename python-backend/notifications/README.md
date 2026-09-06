# notifications/ — Telegram

Deux flux, deux chats, jamais mélangés :

| Chat | Contenu | Variable |
|---|---|---|
| veille | alertes des règles (paliers, dérive d'allocation, sentiment) avec boutons **Exécuté / Ignoré / Reporté** dans le message | `TELEGRAM_CHAT_ID_WATCH` |
| trading | ouvertures, fermetures, résumé quotidien, breaker, kill switch (relais du `Notifier` eToro) | `TELEGRAM_CHAT_ID_TRADING` |

Seuls ces deux `chat_id` sont acceptés par le webhook : tout autre expéditeur est ignoré et journalisé.
Aucun ordre n'est jamais passé depuis Telegram : un clic ne fait que marquer une alerte.

## Mise en place

1. Dans Telegram, ouvrir **@BotFather**, `/newbot`, récupérer le jeton `123456:ABC...`.
2. Créer deux groupes privés (ou utiliser votre chat direct pour l'un des deux) et y ajouter le bot.
3. Récupérer les `chat_id` : envoyer un message dans chaque chat, puis ouvrir
   `https://api.telegram.org/bot<TOKEN>/getUpdates` et lire `message.chat.id` (négatif pour un groupe).
4. Poser les variables sur l'hébergeur (Render) : `TELEGRAM_ENABLED=true`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID_WATCH`, `TELEGRAM_CHAT_ID_TRADING`, `TELEGRAM_WEBHOOK_SECRET` (chaîne longue),
   `TELEGRAM_PUBLIC_URL` (URL publique du backend). Au démarrage, le backend enregistre le webhook
   `<URL>/telegram/webhook` avec le secret.
5. Vérifier : envoyer `/status` dans un des deux chats, le bot répond l'état du module eToro.

Ces valeurs se collent dans le dashboard de l'hébergeur, jamais dans le code ni dans le chat.

## Fonctionnement

- `telegram_service.py` : `send_text`, `send_watch_alert` (clavier inline, `callback_data = alert:<id>:<action>`),
  `send_trading_event`, `answer_callback`, `remove_buttons`, `setup_webhook`. Anti-spam 30 messages / heure
  par chat ; les messages contenant « breaker » ou « kill » passent toujours.
- `api.py` : `POST /telegram/webhook`. Secret comparé en temps constant (401), 503 si non configuré,
  expéditeur hors liste ignoré. Un clic appelle `watch.rules.store.apply_alert_action(id, action, "telegram")`,
  puis retire les boutons du message.
- `etoro/notifier.py` : chaque SMS Twilio est aussi relayé vers le chat trading si Telegram est configuré.

## Retrait de Twilio

Twilio reste en place dans cette PR. Une fois Telegram validé en production (alertes reçues, boutons
fonctionnels, `/status` ok pendant une semaine), retirer `TWILIO_*` de l'hébergeur : le `Notifier`
passe automatiquement en no-op SMS et ne garde que le relais Telegram.
