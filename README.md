# GSWL Data Engine V4.2

Moteur de collecte officiel, contrôlé chaque heure à H:07 UTC.

## Garanties V4.2
- Retries + backoff sur les erreurs réseau transitoires.
- Écriture JSON atomique et validation JSON avant commit.
- Conservation de la dernière observation officielle si une source tombe.
- Séparation stricte entre `engine_checked_at`, `source_checked_at`, `observation_verified_at` et `first_detected_at`.
- Alertes >5 % idempotentes, uniquement au sein de la même série/métrique.
- Une nouvelle source ne remplace jamais une série existante sans définition/unité explicites.
- Adaptateur national BLS actif pour USD; ILOSTAT reste fallback multi-pays.
- Les autres sources restent MONITOR tant que leur série exacte n'est pas validée.
- Workflow protégé contre les collisions de push par `git pull --rebase`.

## Statuts
FRESH = dans le délai attendu de publication; DELAYED = retard; STALE = trop ancien; UNAVAILABLE = aucune observation officielle stockée.
`FALLBACK` décrit la qualité/source et n'est pas un statut de fraîcheur.
