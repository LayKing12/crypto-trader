# agent-full-options-tokens

Projet vierge, prêt pour Claude Code, avec trois packs de skills installés pour travailler avec moins de tokens et de meilleures pratiques. Ancien contenu (crypto-trader) supprimé, historique git effacé.

## Ce qui est installé (`.claude/skills/`, 15 skills)

| Pack | Skills | Source |
|---|---|---|
| **graphify** | `/graphify` : graphe de connaissances du projet ; Claude interroge le graphe avant de lire les fichiers bruts | [safishamsi/graphify](https://github.com/safishamsi/graphify) |
| **ponytail** | 3 skills (`ponytail`, `-review`, `-audit`) : solution la plus courte, réponses concises | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) |
| **agent-skills** | 11 skills génériques : `spec-driven-development`, `planning-and-task-breakdown`, `incremental-implementation`, `test-driven-development`, `debugging-and-error-recovery`, `code-review-and-quality`, `code-simplification`, `git-workflow-and-versioning`, `security-and-hardening`, `context-engineering`, `api-and-interface-design` ; `.claude/references/` contient les checklists qu'ils citent | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) |

## À faire une fois sur ta machine

Graphify a besoin de son exécutable (les hooks de `.claude/settings.json` l'appellent) :

```bash
pip install graphifyy      # Python 3.11.5 à 3.14
```

Puis, quand il y aura du code : `/graphify .` pour construire le graphe, `graphify update .` après chaque modification.

## Skills retirés (jugés inutiles ici)

Retirés pour alléger le contexte : stack ou usage absents de ce projet vide, doublons, ou coût élevé en tokens. Pour en récupérer un : `git log` / `git show` sur le commit précédent, ou le dossier `skills/<nom>` du dépôt source.

- **Frontend / navigateur** : `frontend-ui-engineering`, `accessibility-engineering`, `browser-testing-with-devtools` (exige un serveur MCP chrome-devtools non configuré)
- **Ops / livraison** : `ci-cd-and-automation`, `shipping-and-launch`, `observability-and-instrumentation`, `deprecation-and-migration`, `performance-optimization`
- **Doublons ou coûteux en tokens** : `doubt-driven-development`, `constraint-driven-development`, `source-driven-development` (déjà couvert par Context7), `idea-refine`, `interview-me`, `documentation-and-adrs`, `using-agent-skills`
- **Ponytail, affichage seul** : `ponytail-help`, `ponytail-gain`, `ponytail-debt`

## Notes

- Ponytail est installé en skills seuls, sans ses hooks (pas d'activation automatique, pas d'écriture hors du projet, pas de webhook). Lance `/ponytail` pour l'activer dans une session.
- Les chiffres de réduction de tokens annoncés par graphify (jusqu'à 71x) viennent d'un cas favorable ; attends-toi à beaucoup moins sur un petit projet.
