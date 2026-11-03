"""``ops`` — SPRINT-13 Day-2 operations (D-076 removable; see REMOVABILITY.md).

* ``audit``    — ``admin_actions`` writer (one row per administrative act, same transaction).
* ``flags``    — DB kill switches: ``brands.suspended_at`` + ``platform_flags`` (30 s cache).
* ``guard``    — ``platform_readonly`` ⇒ 503 for every ``/api/v1`` write except auth/admin;
                 ``brand_suspended`` ⇒ 403 for every write on a suspended brand.
* ``router``   — ``/admin/actions``, ``/admin/brands/{id}/suspend|unsuspend``,
                 ``/admin/flags/{key}``, ``/admin/publishing/dead-letter``,
                 ``/admin/publishing/jobs/{id}/requeue``, ``/admin/alerts``.
"""
