# Deploying the public demo

The demo dataset is synthetic and the demo path holds no credentials, so the
dashboard can be deployed publicly without a Jira account existing anywhere in the
picture. Someone reviewing this project gets a URL rather than a clone-and-install.

This is the demo only. Live mode is switched off in the deployment, so the hosted
app can never reach a Jira site even if its configuration were wrong.

## What makes this safe

Three things, and it is worth being explicit about each because "public dashboard"
and "Jira credentials" are words that should not appear comfortably together.

1. **The dashboard never accepts a credential through the UI.** There is no token
   field and never has been. Live mode reads `.env`, which does not exist on the
   deployment.
2. **`JIRALENS_LIVE_JIRA_DISABLED=true` removes the Live option entirely**, so a
   visitor cannot select a mode that would only produce a configuration error. A
   test asserts the radio offers Demo alone when that variable is set.
3. **The engine is read-only regardless.** Even with credentials present, the client
   refuses any verb or path outside a five-entry allowlist. The deployment adds a
   layer; it does not create the guarantee.

## Prerequisites

- The repository is **public**. Streamlit Community Cloud can deploy a private repo,
  but the point here is a link anyone can open. See issue #6 before flipping it.
- A Streamlit Community Cloud account linked to the same GitHub account.

## Steps

1. Go to <https://share.streamlit.io> and sign in with GitHub.
2. **Create app** → **Deploy a public app from GitHub**.
3. Fill in:
   - Repository: `kalungakkabwe/jiralens`
   - Branch: `main`
   - Main file path: `streamlit_app.py`
   - Python version: **3.12** (the package requires 3.12 or newer; Community Cloud
     still defaults to an older interpreter on some accounts, and an unexplained
     install failure is almost always this)
4. Open **Advanced settings** → **Secrets** and paste exactly:

   ```toml
   JIRALENS_LIVE_JIRA_DISABLED = "true"
   ```

   Streamlit exposes secrets as environment variables, which is how
   `pydantic-settings` picks this up. Nothing else belongs here.
5. **Deploy**. First build takes two or three minutes.

## How dependencies resolve

Streamlit Community Cloud installs from `requirements.txt`, not `pyproject.toml`.
The committed `requirements.txt` is a single line:

```
-e .
```

so the real dependency list stays in `pyproject.toml` and the two cannot drift apart.
Pinning versions here instead would create a second source of truth that CI does not
check.

## After deploying

- Confirm the sidebar offers **Demo** only. If **Live Jira** appears, the secret did
  not take; check it is in the app's Secrets panel and not in a `.streamlit`
  directory you created locally.
- Run a scan and open the Methodology tab. That tab is the one worth linking to
  directly from a CV: it states what each check means, the thresholds in use, and
  why there is deliberately no health score.
- Add the URL to the README, just under the Dashboard heading.

Community Cloud sleeps an app after a period with no visitors, and the first request
afterwards takes a few seconds to wake it. That is normal and not worth engineering
around.

## Updating

Every push to `main` redeploys automatically. There is nothing to run.
