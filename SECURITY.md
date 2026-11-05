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

The last two are POST but are retrieval operations — Jira uses POST so a long JQL
string or a thousand issue ids fit in a body rather than a URL.

Enforcement is in code, not in this document. Every request passes through a single
`_request` method that refuses any verb outside GET/POST and any path not matching
`READ_ONLY_ENDPOINTS`, raising `ReadOnlyViolationError`. No `create_issue`,
`update_issue`, `transition_issue`, `add_comment` or `delete_issue` method exists.

Three tests back that up: one asserts none of those method names is on the client's
public surface, one asserts the allowlist holds exactly five operations so widening
it is a deliberate change, and one runs a complete scan with a catch-all route
registered for PUT, PATCH and DELETE and asserts it was never called.

### Prefer a scoped token

Atlassian's scoped API tokens let you grant `read:jira-work` and nothing else, so the
credential cannot write even if the code tried. Set `JIRA_CLOUD_ID` to use one. This
is defence in depth: the allowlist stops JiraLens writing, and the token scope stops
anything holding that token writing.

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
