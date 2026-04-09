# knowledge/

This directory holds the company's knowledge base that feeds the **smart-brain** AI system.

## Subdirectories

| Folder        | Purpose                                               |
|---------------|-------------------------------------------------------|
| `jira/`       | Exported Jira tickets, epics, or sprint summaries     |
| `confluence/` | Confluence pages exported as Markdown or Excel        |
| `general/`    | Any other Markdown or Excel documents                 |

## Supported file formats

* **Markdown** (`.md`) – write or paste Confluence/Jira exports directly
* **Excel** (`.xlsx`, `.xls`) – upload spreadsheets exported from Jira/Confluence

## How to add new knowledge

1. Drop your `.md` or `.xlsx` file into the appropriate subfolder.
2. Commit and push.  The [GitHub Actions workflow](../.github/workflows/index.yml)
   will automatically re-index the knowledge base.
3. Optionally pull the updated `index/chunks.jsonl` in your AI tool and rebuild
   your local vector store.
