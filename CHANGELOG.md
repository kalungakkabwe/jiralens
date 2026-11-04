# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Phase 0: packaging, validated configuration, exception hierarchy, logging with
  credential redaction, CI workflow.
- Phase 1: typed domain models with an injected clock, Jira issue normaliser keyed on
  `statusCategory.key`, `IssueSource` protocol, and a synthetic demo source with ~50
  fixtures covering every audit condition plus the known boundary cases.

### Not yet implemented

- Phase 2 check engine, Phase 3 orchestration, Phase 4 reports, Phase 5 CLI scan,
  Phase 6/7 live Jira client, Phase 8 Streamlit dashboard.
