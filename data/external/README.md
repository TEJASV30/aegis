# External benchmark cache

The benchmark command downloads OpenML data set 1597 into this ignored directory.
The generated evidence records the OpenML data ID, source URL, license, split policy,
sample counts, measurement time, and limitations. The raw third-party data is not
committed to this repository.

Run `python -m fraud_platform.evaluation.external_benchmark` from the project
environment to reproduce `reports/external_benchmark.json`.
