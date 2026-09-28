# Correction evidence storage

The September 25, 2026 artifact was rejected at 790,392,832 bytes, above the
unchanged 512 MiB delivery limit. Its duplicate-source correction table occupied
436,248,576 bytes: 318,644 dated receipts repeated only 3,590 distinct JSON
proofs. September 9 had 25,290 receipts and a 34,365,440-byte correction table.
Meanwhile stop times fell from 1,958,122 to 1,567,058. This was repeated audit
evidence, not an increase in the number of journeys.

Finalization now stores each distinct proof once and references it from dated
receipts. The original correction relation names are read-only SQL views with
the same columns and exact JSON strings. Reconciliation decisions and all
calendars, trips, and stop times remain unchanged. Build reconciliation runs
before this final read-only representation is created.

On a local copy of the exact rejected artifact, finalization reduced the file
to 361,455,616 bytes. Ordered row hashes of all 14 original tables/relations
matched before and after; SQLite integrity_check passed. The original artifact
remains intact. Production must receive a newly built, manifested artifact
through normal validation and promotion, not the locally modified copy.

CI checks the same database size constant used by delivery before publishing.
Archive size rejection records the allowed filename, actual bytes and limit;
no archive path or secret is added to monitoring state.

## Growth from source-edition repairs (September 2026)

The successor-edition calendar repair and the lineage duplicate retirements add
many more receipts. A dry run on the 28 September candidate produced 5,947
calendar corrections and 4,040 duplicate trips covering 442,316 dates. After
compaction the database was 419,966,976 bytes. That is about 18% under the
512 MiB GitHub limit, just short of the 20% margin this design aims for. If a
fresh build lands closer to the limit, store duplicate receipts as date ranges
per trip and basis (as calendar corrections already do) before loosening the
margin.
