# WECA bus punctuality audit

An independent, non-commercial measurement of bus punctuality across the West
of England, built from operators' public open data and compared with the dated
annual area-wide target in the Enhanced Partnership Scheme. The current
2026-27 target is 87%; the separate long-term goal is 95% by 2030.

**Live audit:** https://bristol-bus-bot.github.io/weca-bus-audit/

The static interface is maintained in the `audit-site/` directory of the
[BristolBusBot source repository](https://github.com/bristol-bus-bot/bristolbusbot).
Collection, matching and aggregation code is in `collector/` and `pipeline/`.
The publication job copies the interface and generated aggregate data to the
separate `weca-bus-audit` GitHub Pages repository.

The full definitions, data sources and limitations are in the
[published methodology](https://github.com/bristol-bus-bot/weca-bus-audit/blob/main/AUDIT_METHODOLOGY.md).
The audit is independent and may differ from figures produced with different
sampling or matching methods.

Dated local committee briefings, when prepared, live under `docs/packs/` and
carry their own HTML page, downloadable PDF and versioned JSON summary. The
nightly publisher adds new packs without deleting older permanent addresses.

Contains public sector information licensed under the Open Government Licence
v3.0. The Department for Transport and its agencies accept no responsibility
for the accuracy, timeliness or completeness of the data. Project code is
licensed under AGPL-3.0-only except where otherwise noted.

Not affiliated with, endorsed by, or funded by any bus operator, the West of
England Combined Authority, or any authority.
