# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately using GitHub's
**private vulnerability reporting** (Security → Report a vulnerability on
this repository). That route reaches the maintainer directly without
disclosing the issue publicly.

Ordinary bugs — wrong delays, broken layout, a confused departure board
— belong in public issues, not security reports.

## Scope notes

- The public website is a read-only consumer of locally collected open
  data. It has no user accounts, stores no visitor data and serves no
  third-party scripts.
- The bot's control API binds to loopback on the production host and
  requires a bearer token; it is not internet-reachable.
- Secrets are never stored in this repository; CI scans both the working
  tree and full history on every push.

Reports about the underlying open-data feeds (BODS, TNDS, NaPTAN) should
go to their operators, though a heads-up is appreciated if this project
mishandles their data.
