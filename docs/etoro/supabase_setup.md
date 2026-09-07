# Supabase : état persistant du bot

Projet `cryptomind`, région eu-west-1, offre gratuite. Créé le 2026-09-06, tables migrées.

| | |
|---|---|
| Ref projet | `zawdnmxvtsrnshfkvcoc` |
| URL API (`SUPABASE_URL`) | `https://zawdnmxvtsrnshfkvcoc.supabase.co` |
| Dashboard | https://supabase.com/dashboard/project/zawdnmxvtsrnshfkvcoc |

## Clé à poser sur Render

Supabase › Project Settings › API Keys › **service_role** (secret). C'est `SUPABASE_SERVICE_KEY`.
Jamais la clé `anon` : les tables ont RLS activé sans politique, seule la clé service peut lire et écrire.
Ne collez cette clé que dans Render, jamais dans le code ni dans le chat.

## Tables

- `etoro_state (id text pk, data jsonb, updated_at)` : une seule ligne, `id = 'main'`, contenant
  l'état complet (positions suivies, cooldowns, PnL du jour, equity de départ, breaker, kill switch).
  Écrite à chaque `StateStore.save()`, relue au démarrage.
- `etoro_decisions (id, at, kind, symbol, data jsonb, created_at)` : une ligne par décision de l'agent
  (signal, refus, ouverture, fermeture, breaker, kill switch, erreur). Relue au démarrage (500 dernières).

## Requêtes utiles (SQL Editor)

État courant :

```sql
select data->>'daily_pnl' as pnl_jour, data->>'equity_start_of_day' as equity,
       data->>'breaker_until' as breaker, data->>'kill_switch' as kill, updated_at
from etoro_state where id = 'main';
```

Vingt dernières décisions :

```sql
select at, kind, symbol, data->>'reason' as raison, data->>'action' as action
from etoro_decisions order by at desc limit 20;
```

PnL réalisé par jour :

```sql
select date(at) as jour, count(*) as fermetures, round(sum((data->>'realized_pnl')::numeric), 2) as pnl
from etoro_decisions where kind = 'close' group by 1 order by 1 desc;
```

Vider le journal (l'état n'est pas touché) :

```sql
delete from etoro_decisions;
```

## Comportement en cas de panne Supabase

Le bot n'échoue jamais sur Supabase : chaque appel a un délai de 5 s et une erreur est seulement
journalisée. Le cache JSON local prend le relais jusqu'au redémarrage suivant.
