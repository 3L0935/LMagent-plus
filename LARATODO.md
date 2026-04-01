# LMAgent-Plus — TODO.md

> Premier fichier à ouvrir après CLAUDE.md.
> Si ce fichier est vide (ou ne contient que ce header) → lire PLAN.md et démarrer la phase suivante.
> Cocher chaque tâche au fur et à mesure. Quand tout est coché → vider ce fichier et passer à la phase suivante dans PLAN.md.

---

Phase 5.2 terminée et validée. Branche `lara` mergée sur `main` (2026-04-02).
Bugs post-merge corrigés :
- [x] Fix `routing.default` non mis à jour par `/setup` wizard (maintenant forcé à `"local"` avec le backend)
- [x] Fix `asyncio.get_event_loop()` déprécié → `get_running_loop()` dans `_start_setup_wizard`

Limitation connue (non bloquante) :
- `yaml.dump` dans `_apply_wizard` écrase les commentaires de `config.yaml` — à adresser si /setup devient un flux critique

Prochaine phase : **Phase 6** (Desktop GUI — Tauri + Svelte) ou **Phase 7** (Installer).
Lire PLAN.md § "v0.2 Phases" pour les tâches et prérequis.
