"""M6 — WhatsApp Cloud API conversations (SPRINT-09 Part B).

Governing rules (directive §2-0): no message reaches a customer without a human actor
(every outbound row carries ``sent_by_user_id``; AI suggestions live in ``wa_reply_drafts``
and never touch the network); webhook signatures are mandatory (``X-Hub-Signature-256``);
system tokens travel only as ``Authorization: Bearer`` (D-064); side-effecting POSTs are
sent exactly once (D-070); PyWa is a read-only reference (D-071) — the client here is
hand-written on the shared ``httpx`` pool.
"""
