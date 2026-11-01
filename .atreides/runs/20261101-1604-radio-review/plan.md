# Run 20261101-1604-radio-review

## Request (verbatim)
I want the dev team to go over this project, verify the code's integrity, and get familiarized with this project, as this is going to be one of the main projects you will be working on. Project: ZeRock Radio, repo /Users/rkuperman/zerock-sync/zerock-radio (read CLAUDE.md there first). This is a READ-ONLY review and familiarization run: no product code edits, no deploys, no service restarts, no commits/pushes (the repo is PUBLIC and auto-pushed by launchd; never print secrets). Five independent read-only reviewers are already running in the background (core schedule/upload pipeline, WordPress sync + website, audio engine, security/secrets audit incl. git history, deployed-vs-local parity + server health); reuse their findings when their notifications arrive instead of duplicating that work.

## Understanding
A review-and-onboarding run, not a feature. Deliverables: (1) a project map the whole team can work from, (2) a verified, ranked list of integrity problems (code, security, deployed-vs-repo drift, health). No product change is made in this run; fixes are proposed for the customer to approve as follow-up runs.

## Acceptance criteria
1. design.md: how the system works end to end (hosts, services, data files, request/upload/air flow, WP board, audio chain), written so a new developer can navigate it.
2. design.md / review.md: ranked integrity findings, each with evidence (file:line, command + result) and marked verified or suspect; duplicates across the five reviewers merged; contradictions between reviewers resolved by re-checking the code.
3. Production vs repo parity table and server health summary.
4. Security: what is exposed in the PUBLIC repo (working tree and history) and what must be rotated. Never print secret values.
5. A proposed, prioritised fix backlog (not implemented).
6. Nothing modified: no product files, no deploy, no restart, no commit/push.

## Entry stage and why
Entry = Thufir (research & design): the request is an investigation, nothing precise to implement yet.
Planned path: Thufir -> Jessica -> Gurney (safe checks only) -> Paul.
Skipped by Leto, with reasons:
- Yueh and Duncan: no code change is requested; the only product-file editor stays idle.
- Edric: no commit/push/PR. The repo is public and auto-pushed; nothing may be committed.
- Gurney is NOT skipped but is limited to non-invasive checks (compile, lint, syntax checks, any existing tests that do not touch live services). No test files are to be edited in this run.

## Deviations from the standard pipeline (decided by Leto)
- UPDATE (infrastructure rework 1): the write guard only lets agents write under .atreides/ of /Users/rkuperman/Documents/Claude_Code/ZeRock_Uploader (the session project). The run folder was therefore moved to /Users/rkuperman/Documents/Claude_Code/ZeRock_Uploader/.atreides/runs/20261101-1604-radio-review. That folder belongs to a different, internal repo (github.intuit.com) that the launchd job does NOT sync (it syncs only /Users/rkuperman/zerock-sync), and .atreides/ is excluded locally via .git/info/exclude so it cannot be added by accident. Original reasoning kept for the record: run folder outside the radio repository instead of .atreides/ inside the project: the repo is auto-committed and pushed to a PUBLIC GitHub repo every 5 minutes, and this run's artifacts contain a security audit. atreides-init was not run for the same reason (it would edit CLAUDE.md and create .atreides/ in the tree).
- Inputs: five reviewer reports from background agents are copied into inputs/ when they arrive; Thufir and Jessica build on them.

## Out of scope
Fixing anything, rotating secrets, deploying, restarting services, touching 192.168.1.114 (password-only, not needed).
