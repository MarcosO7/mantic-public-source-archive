# Public CSL source archive

This repository preserves only the public US International Trade Administration Consolidated Screening List source and its response metadata. It contains no private workspace files or credentials.

Source: https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json

The target collection cutoff is **1/15/2027 at 12:00 UTC**. Three independent GitHub Actions schedule triggers run at 11:17, 11:47 and 12:07 UTC on January 15. A year guard permits production collection only in 2027. Before-cutoff jobs poll original version headers with one-byte GET range requests every 30 seconds through 12:08 UTC. Changed versions trigger complete captures. Additional captures are attempted at 11:55, 11:59:50, 12:00:05, 12:01 and 12:07 when timing permits. Every complete-response attempt receives a separate public GitHub Release with original JSON bytes, original response headers, UTC timestamps, completeness checks and SHA-256 hashes. Releases are never overwritten by the collector.

**A test release is a current observation. It does not obtain or certify the future January snapshot.** Even production releases require reconciliation to establish which complete official version was latest and publicly available at the cutoff. Hashes establish byte identity, not original publication timing by themselves.

A meaningful weekly source-header observation is committed on Sundays at 15:37 UTC until 1/19/2027. This monitors source refresh behavior and supplies repository activity across the more than 60 days before collection. GitHub documents that schedules in inactive public repositories can be disabled after 60 days. Scheduled jobs can also run late or be dropped; overlapping collection triggers and a separate local backup reduce, but do not eliminate, that risk. Work after the requested preservation period is a no-op; the annual collection guard excludes other years.

The workflow uses standard `ubuntu-24.04` public-repository runners, without paid larger runners or Actions artifact storage. Evidence is published as public Release assets. The workflow's ordinary repository token is supplied by GitHub at runtime and is never written into source or receipts.

Official operational references:

- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows
- https://docs.github.com/en/actions/how-tos/troubleshoot-workflows
- https://docs.github.com/en/actions/concepts/billing-and-usage

Manual test: run the **Preserve public CSL source** workflow with mode `test`.

After the final January verification, disable the schedule with:

```sh
gh workflow disable csl-preserve.yml --repo MarcosO7/mantic-public-source-archive
```

After 1/19/2027 the weekly observation code is a no-op, although GitHub may still start that no-op workflow until it is disabled or suspended for inactivity. Publication and public-byte verification are synchronous, so the 30-second polling cadence and precise checkpoints are best effort. A failed upload or completeness check can terminate one job; the overlapping cloud triggers and separately registered local backup are retained for that reason.
