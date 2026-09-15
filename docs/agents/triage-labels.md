# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## State on GitHub

All five labels exist on `msobroza/pydocs-mcp` as of 2026-09-15. If a fresh fork or mirror lacks them, create them once with:

```bash
gh label create needs-triage    --color E4E669 --description "Maintainer needs to evaluate this issue"
gh label create needs-info      --color D876E3 --description "Waiting on reporter for more information"
gh label create ready-for-agent --color 0E8A16 --description "Fully specified, ready for an AFK agent"
gh label create ready-for-human --color 1D76DB --description "Requires human implementation"
```

`gh label create` fails on a label that already exists, so the commands are safe to re-run one at a time.
