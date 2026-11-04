# Security

## Reporting a vulnerability

Open a private security advisory through the repository's Security tab rather than a
public issue.

## Credential handling

- The API token is read from the environment or a local `.env`. `.env` was added to
  `.gitignore` before the first commit.
- The token is never accepted as a command-line argument: shell history retains it.
- It is held as a pydantic `SecretStr`, so it cannot surface through `repr()`, a
  validation error, a traceback or a logged model dump.
- All logging passes through `RedactingFilter`, which scrubs registered secrets and
  anything shaped like an `Authorization` header — the usual way a token escapes, via
  an HTTP request dump.
- No report format embeds credentials.
- A dedicated read-only Jira service account is preferred where policy allows.

## Read-only enforcement

JiraLens reaches only these endpoints:

```
GET  /rest/api/3/myself
GET  /rest/api/3/serverInfo
GET  /rest/api/3/project/{projectKey}
POST /rest/api/3/search/jql
POST /rest/api/3/changelog/bulkfetch
```

The last two are POST but are retrieval operations. The client refuses any request
whose method is not GET or POST, or whose path is not on the allowlist, raising
`ReadOnlyViolationError`. No `create_issue`, `update_issue`, `transition_issue`,
`add_comment` or `delete_issue` method exists, and a test asserts that a complete
scan emits no PUT, PATCH or DELETE.

## Data minimisation

JiraLens requests only the fields it audits. It does not retrieve descriptions,
comments, attachments, worklogs, customer contact details or unrestricted custom
fields — so none of that can reach memory, a report or a log.

## Export safety

- CSV cells beginning `=`, `+`, `-` or `@` are prefix-escaped, so a Jira summary
  cannot execute as a formula when an administrator opens the export in Excel.
- The HTML report renders through Jinja2 with autoescaping on and embeds no scripts.

## Public deployment

Set `JIRALENS_LIVE_JIRA_DISABLED=true` on any publicly reachable deployment. A
production Jira token must never be placed in a public demo.
