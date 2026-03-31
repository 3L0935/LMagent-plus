# LMAgent-Plus — CLAUDE.md

Orchestrateur d'agents IA local-first, self-contained.
Télécharge et gère llama.cpp en interne, gère les modèles locaux (.gguf),
supporte les APIs cloud (Anthropic, OpenAI) pour de l'orchestration mixte.

---

## Avant de faire quoi que ce soit

1. Lire `TODO.md` — si des tâches sont listées, les traiter en priorité
2. Si `TODO.md` est vide → lire `PLAN.md` pour identifier la phase suivante
3. Mettre à jour `TODO.md` avec les tâches de la nouvelle phase avant de commencer

---

## Fichiers de référence

| Fichier | Contenu |
|---------|---------|
| `PLAN.md` | Phases du projet, statut de chaque phase, objectifs |
| `TODO.md` | Tâches concrètes de la phase en cours |
| `docs/ARCHITECTURE.md` | Structure du repo, conventions de code |
| `docs/MEMORY.md` | Système de mémoire à deux couches (global + par agent) |
| `docs/RUNTIME.md` | Détection backend llama.cpp, gestion modèles, lifecycle |
| `docs/PERSONAS.md` | Format des personas YAML, règles system prompt |
| `docs/USER_DIR.md` | Structure complète de `~/.lmagent-plus/` |

---

## Règles absolues

- Secrets uniquement via variables d'environnement — jamais dans les fichiers
- Paths toujours via `pathlib.Path`, jamais de strings brutes
- Tout ce qui est propre à l'user vit dans `~/.lmagent-plus/` — jamais dans le repo
- Actions destructives (rm, overwrite, download) → toujours confirmer avant
- Après chaque tâche complétée → mettre à jour `TODO.md`
