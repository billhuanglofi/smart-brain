# knowledge/

This directory holds the company's knowledge base that feeds the **smart-brain** AI system.

## Subdirectories

| Folder        | Purpose                                               |
|---------------|-------------------------------------------------------|
| `jira/`       | Exported Jira tickets, epics, or sprint summaries     |
| `confluence/` | Confluence pages exported as Markdown or Excel        |
| `general/`    | Any other Markdown, Excel, or CSV documents           |

## Supported file formats

* **Markdown** (`.md`) – write or paste Confluence/Jira exports directly
* **Excel** (`.xlsx`, `.xls`) – spreadsheets exported from Jira/Confluence
* **CSV** (`.csv`) – tabular exports from any tool

## How to add new knowledge

Drop your file into the appropriate subfolder and run `upload.py` from your
laptop to push it into the local ChromaDB vector database:

```bash
# Upload a single file
python scripts/upload.py knowledge/jira/my_ticket.md

# Upload everything in a folder at once
python scripts/upload.py knowledge/

# Fetch directly from Jira or Confluence (no file needed)
python scripts/upload.py --jira PROJ-123
python scripts/upload.py --confluence https://your-company.atlassian.net/wiki/spaces/ENG/pages/123456
```
