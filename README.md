# agent-full-options-tokens

Projet vierge, prêt pour Claude Code, avec trois packs de skills installés pour travailler avec moins de tokens et de meilleures pratiques. Ancien contenu (crypto-trader) supprimé, historique git effacé.

## Ce qui est installé (`.claude/skills/`, 33 skills)

| Pack | Skills | Source |
|---|---|---|
| **graphify** | `/graphify` : graphe de connaissances du projet ; Claude interroge le graphe avant de lire les fichiers bruts | [safishamsi/graphify](https://github.com/safishamsi/graphify) |
| **ponytail** | 6 skills (`ponytail`, `-review`, `-audit`, `-debt`, `-gain`, `-help`) : solution la plus courte, réponses concises | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) |
| **agent-skills** | 26 skills de cycle de vie (spec, plan, TDD, revue, sécurité, ship…) ; `.claude/references/` contient les checklists qu'ils citent | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) |

## À faire une fois sur ta machine

Graphify a besoin de son exécutable (les hooks de `.claude/settings.json` l'appellent) :

```bash
pip install graphifyy      # Python 3.11.5 à 3.14
```

Puis, quand il y aura du code : `/graphify .` pour construire le graphe, `graphify update .` après chaque modification.

## Notes

- Ponytail est installé en skills seuls, sans ses hooks (pas d'activation automatique, pas d'écriture hors du projet, pas de webhook). Lance `/ponytail` pour l'activer dans une session.
- Les chiffres de réduction de tokens annoncés par graphify (jusqu'à 71x) viennent d'un cas favorable ; attends-toi à beaucoup moins sur un petit projet.
