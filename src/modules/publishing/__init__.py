"""``publishing`` — scheduled publishing to Meta behind human approval (SPRINT-07, M4).

``publish_jobs`` is the work queue (one active job per item), ``publish_attempts`` the
D-055 evidence trail (one row per Graph call, written *before* the call). The
``PostgresScheduler`` worker leases due jobs with ``FOR UPDATE SKIP LOCKED``; the
``MetaPublisher`` performs the external call with the Page token decrypted only for the
duration of the call. No silent repetition: an attempt without an outcome after its lease
expires is ``ambiguous`` and the job fails for a human to verify (§2-0 c).
"""
