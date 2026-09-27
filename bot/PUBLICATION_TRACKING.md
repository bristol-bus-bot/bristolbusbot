# Publication tracking

Confirmed posts are recorded beside editorial usage in `.publications.json`.
The ledger keeps at most 90 days / 10,000 posts, including URI, text, subject,
fact IDs actually used, fallback status/reason and prompt version. Drafts and
failed publications are not recorded; duplicate confirmation URIs are ignored.
Old posts are not retrospectively classified from text.

The existing hourly follower task samples likes/reposts once at age 24–26 hours.
Only counts are retained, never liker handles. A missed window stays missing.
This narrow shared age window replaces a nightly sweep of differently aged posts.
The sibling `.report.json` is refreshed with a rolling seven-day report, grouped
by subject and prompt version, and an explicit fallback denominator. Counts are
descriptive, not causal evidence for an editorial preference.

Metrics failures do not affect posting. No extra Gemini calls are involved.
The report is local; automated Slack delivery and a full week of observations
remain separate acceptance steps. Do not claim seven days of results on launch.
