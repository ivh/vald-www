# VALD email — delivery model and the auth'd-mail.uu.se future

Written 2026-08-06. Supersedes the earlier `email.md`, whose application-side notes
were correct but whose transport diagnosis ("smtp.uu.se silently quarantines
internal recipients; mail.uu.se is the answer") turned out to be confounded. The
real bug was **sender-address alignment**, not the relay and not authentication.
`email_reqs.md` (legacy email-request path, retirement analysis) is separate and
still stands.

## TL;DR

- **The "missing" mail was never lost — it was delayed hours inside SUNET's Halon
  filter and then delivered.** A recovered message sat at `mailfilter-ng-4.sunet.se`
  for **6 h 16 m** (submitted 10:22, delivered 16:38 CEST, 2026-08-06) with
  `dmarc=pass` throughout. Not a drop, not an anti-spoof quarantine, not DMARC, not
  neon.
- **neon is not implicated.** It handed the message to the university relay in one
  second, and DKIM/SPF/DMARC all pass — sender alignment was never the problem.
- **The `smtp_generic_maps` → `sender_canonical_maps` change is very likely a red
  herring.** The delayed mail delivered on its own once the SUNET backlog drained;
  our config didn't fix it. Keep the canonical map as harmless hygiene — but don't
  credit it. Full timeline and the demoted earlier theory in §1.
- **Unauthenticated relay via `relayhost = smtp.uu.se` delivers to both internal and
  external recipients** — confirmed by actual receipt (`AuthAs: Anonymous`,
  `dmarc=pass`). Auth is *not* required for delivery.
- **This is a SUNET/ITS-side issue.** Report it with the recovered headers; there is
  nothing to fix in neon's postfix. SASL / auth'd `mail.uu.se` (§6) is
  deferred-strategic and is *not* the solution to this.

## 1. What actually broke

Symptom, as it looked at the time: mail left neon with `status=sent`, no bounce, and
did not arrive — read as "silently lost." **It was not lost. It was delayed hours in
SUNET's Halon filter, then delivered.** A recovered "missing" message (`vald test A`,
From `vald@physics.uu.se`) gives the timeline from its `Received:` chain:

| Time (CEST) | Hop | |
|---|---|---|
| 10:22:27 | submitted on neon (`mail`, uid 1002) | From vald@physics.uu.se |
| 10:22:28 | neon → velox.its.uu.se | +1 s |
| 10:22:29 | velox → **mailfilter-ng-4.sunet.se** (Halon, 89.45.235.5) | reached SUNET |
| **↓** | **held here 6 h 16 m** | **the delay lives at this hop** |
| 16:38:47 | mailfilter-ng-4 → cursorius.its.uu.se | released |
| 16:38:47 | smtp-out6 → mailfilter-ng-3 (central DKIM signing) | `dmarc=pass` |
| 16:38:49 | → Exchange → mailbox | delivered, `AuthAs: Anonymous` |

What this establishes:

- **neon did its job in one second.** No local queue delay, no SASL/relay deferral.
  The relay, SASL, and sender-rewrite investigations were all the wrong layer.
- **The ~6 h delay was entirely inside `mailfilter-ng-4.sunet.se`** — one SUNET Halon
  node (cloud instance `89.45.235.5`). Later mail via the healthy `mailfilter-ng-3`
  (`192.36.171.200`) flowed in ~0.2 s. Signature of a **backlog / hold on one SUNET
  node**, plausibly an RPD/reputation deferral (`X-Halon-RPD-*` headers) or a node
  backlog on the day.
- **DMARC/DKIM/SPF all pass** on the delayed message (`dkim=pass d=physics.uu.se
  s=halon`, `dmarc=pass header.from=physics.uu.se`). **Sender alignment was never the
  problem.**

### The earlier theory (demoted — kept for honesty)

Before the delayed mail resurfaced, the working hypothesis was a DMARC / Exchange
anti-spoof **quarantine** caused by `smtp_generic_maps` leaving `header.from`
unaligned, "fixed" by moving `vald@physics.uu.se` to `sender_canonical_maps`. The
recovered header kills this: the same sender passed DMARC cleanly *and* was still
delayed 6 h. The map change is almost certainly **coincidental** — mail resumed
because the SUNET backlog drained, not because of the rewrite. Keep the canonical map
as reasonable hygiene (§4 — rewriting local senders to a real address is fine), but
do **not** credit it with the fix, and do not chase relay/SASL/rewrite on a
recurrence — look at SUNET.

## 2. The delivery model (corrected)

Mail from neon does **not** go straight to the recipient's Exchange mailbox. It
hairpins out through the university's central chain and back in:

```
neon → smtp.uu.se (lyra.its.uu.se, 130.238.7.73, :25)
     → mailfilter-ng-N.sunet.se (Halon)  →  smtp-out6.uu.se (130.238.7.177)   [OUT: central DKIM signing]
     → mailfilter-ng → cursorius → velox → smtp.user.uu.se                     [back IN]
     → UUC-EPOST… (Exchange) → mailbox
```

Consequences:

- The central chain adds two DKIM signatures: `d=physics.uu.se s=halon` (aligned to
  the From domain) and `d=uu.se s=centralsmtp2` (org-domain). Their presence in a
  delivered message is the fingerprint that it went through the sanctioned signing
  path.
- The message **re-enters Exchange as `X-UU-Exchange-Origin: External`,
  `AuthAs: Anonymous`.** Exchange then applies anti-spoofing: a uu.se-claiming
  external message is **delivered iff DMARC passes, silently quarantined if it
  fails.** That is the whole game.
- So the relay choice (smtp.uu.se vs mail.uu.se) is **not** the discriminator the old
  note claimed. `smtp.uu.se` delivers internal mail fine when the sender is aligned —
  confirmed for both `astro.uu.se` and `physics.uu.se` senders. The discriminator is
  DMARC alignment of the From domain.

## 3. "Will it deliver internally?" — the header checklist

Read any delivered copy; all green = will not be quarantined:

| Header | Pass value | Why |
|---|---|---|
| `Authentication-Results: dmarc=` | `pass`, `header.from=<domain you send as>` | The gate. |
| `dkim=` | `pass`, `header.d=` = your From domain (exact) or `uu.se` (relaxed org-domain) | DKIM alignment carries DMARC here; don't lean on SPF (envelope gets rewritten). |
| `DKIM-Signature:` ×2 | `d=<From domain> s=halon` **and** `d=uu.se s=centralsmtp2` | Proves the central signing hairpin handled it. Missing `s=halon` for your domain ⇒ danger. |
| `AuthAs` / `Origin` | `Anonymous` / `External` | Expected on this path — *not* a red flag. |

Test the **exact** production `DEFAULT_FROM_EMAIL` / `VALD_ADMIN_EMAIL` values —
alignment is per-domain.

## 4. sender_canonical_maps — recommended form

`main.cf`:
```
sender_canonical_maps = hash:/etc/postfix/sender_canonical
```
`/etc/postfix/sender_canonical` (adapt to the real local senders on neon — enumerate
cron/app/root output first):
```
vald@neon.physics.uu.se      vald@physics.uu.se
vald                         vald@physics.uu.se
root                         vald@physics.uu.se
root@neon.physics.uu.se      vald@physics.uu.se
www-data                     vald@physics.uu.se
www-data@neon.physics.uu.se  vald@physics.uu.se
```
Then:
```
sudo postmap /etc/postfix/sender_canonical && sudo postfix reload
```
Notes:
- `canonical_classes` default includes `header_sender`, so this rewrites the `From:`
  header too — that's the point. For locally-submitted mail (the Django app via
  `localhost`) header rewriting is active by default (`local_header_rewrite_clients`).
- A domain catch-all (`@neon.physics.uu.se  vald@physics.uu.se`) would guarantee
  nothing escapes as a local address, but it rewrites *every* sender from that
  domain — fine on an app/cron-only host, reckless on a multi-user one. neon is
  effectively the former; use with eyes open.
- Anything that must keep its own identity (e.g. a real user's astro address) must
  NOT be caught by a broad rule — list specific keys instead.

## 5. Still to confirm

The recovered header (§1) already answered the big question — SUNET Halon delay, not
a drop, not alignment. Remaining, lower-stakes checks:

1. **Confirm the pattern across the other recovered messages.** Read each one's
   `Received:` chain bottom-up; if they all show the multi-hour gap at
   `mailfilter-ng-*.sunet.se`, it is conclusively a SUNET-side delay and neon/config
   is fully exonerated.
2. **Report to ITS/SUNET** with the recovered headers (the 6 h residence at
   `mailfilter-ng-4.sunet.se`), and ask whether it was an RPD/reputation hold or a
   node backlog — and whether it can recur. This is the only real fix; there is
   nothing to change in neon's postfix.

## 6. SASL / auth'd mail.uu.se — deferred-strategic

Not needed for delivery today, but the uu admins asked (last year) that we move off
`smtp.uu.se` to authenticated submission via `mail.uu.se`. Captured for when that
lands:

- **Endpoint:** `mail.uu.se` (130.238.62.31), 587 STARTTLS / 465.
- **The blocker:** postfix authenticates against the **pre-STARTTLS** EHLO mechanism
  list, which advertises only `AUTH GSSAPI NTLM`. `LOGIN` is offered **only after
  STARTTLS**:
  ```
  openssl s_client -starttls smtp -connect mail.uu.se:587 -crlf
  EHLO neon.physics.uu.se
  250-AUTH GSSAPI NTLM LOGIN      ← LOGIN present only here
  ```
  Postfix has never logged a successful auth; it reports
  `offered no supported AUTH mechanisms: 'GSSAPI NTLM'` and sends unauthenticated.
- **Not** a security-options problem: `smtp_sasl_security_options = noanonymous`
  (not `noplaintext`), no `smtp_sasl_tls_security_options` override — postfix *is*
  willing to use LOGIN.
- **Fix candidate — 465 wrappermode (implicit TLS)** so the only EHLO postfix ever
  sees is already encrypted and includes LOGIN:
  ```
  # master.cf
  smtps-relay  unix  -  -  n  -  -  smtp
      -o smtp_tls_wrappermode=yes
      -o smtp_tls_security_level=encrypt
  ```
  route via `sender_relay`: `vald@physics.uu.se  smtps-relay:[mail.uu.se]:465`.
  **Confirm 465 is reachable first** — an earlier `openssl … :465` probe from neon
  came back empty (inconclusive). Test against the IP postfix uses (130.238.62.31).
- **Coupling:** sender-dependent auth is keyed by envelope sender
  (`smtp_sender_dependent_authentication = yes`). You cannot validate the
  `vald@physics.uu.se` account's auth until you have its credentials AND
  `VALD_FROM_EMAIL = vald@physics.uu.se`.
- On cutover, re-run the §3 checklist to a fresh external + internal address and
  confirm `dmarc=pass` still holds through the authenticated path.

## 7. Application side (carried forward — still valid)

Bodies are Django templates in `vald/templates/vald/email/`, rendered with
`render_to_string`. Keep the `{% autoescape off %}` wrapper in any new one
(plain-text mail; escaping mangles apostrophes and `&`).

| Occasion | Recipient | Template | Call site |
|---|---|---|---|
| Activation, self-service | user | `activation.txt` (`approved=False`) | `views.py` login |
| Activation, admin approval | user | `activation.txt` (`approved=True`) | `admin.py` action |
| Password reset | user | `password_reset.txt` | `views.py` |
| Results ready | user | `results_ready.txt` | `views.py` background thread |
| New registration | `VALD_ADMIN_EMAIL` | `new_registration.txt` | `views.py` (~L744) |
| Queue full | `VALD_WEBMASTER_EMAIL` | `queue_full.txt` | `backend.py` |
| Contact form | admin or webmaster (user picks) | `requests/contact-req.txt` | `views.py` |
| Unhandled 500 | `ADMINS` = webmaster | Django built-in | `settings_deploy.py` LOGGING |

- Contact form uses the older `$var` substitution
  (`utils.py:render_request_template`, files in `requests/`) — left alone.
- Both activation variants share one template so wording can't drift.
- Link expiry text from `VALD_TOKEN_MAX_AGE_DAYS`; retention from
  `Request.retention_description()`. Neither hardcoded.
- Queue-full alert throttled to one per `VALD_QUEUE_FULL_COOLDOWN` (1800s) via
  `cache.add()`.
- Registration notify is wrapped `try/except` + `logger.exception`
  (`fail_silently=False`) so a mail failure can't undo the registration.
- No confirmation mail to the registrant — deliberate.

Four app-side branches return **before** any mail is sent and leave no postfix
trace; if a user reports no mail and postfix shows nothing, it's one of these:
1. account already has a password (`needs_activation()` false)
2. `is_active=False` — awaiting approval, returns early
3. login rate limit, 5 POST/min per IP (`rm -rf cache/ratelimit/*`)
4. SMTP failure — surfaces as a red UI message

## 8. Operational gotchas

- **Silent-delay trap.** Once postfix hands off (`status=sent`), anything downstream
  — a multi-hour SUNET hold, a quarantine, a backlog — is **invisible to the app**:
  `send_mail` returns clean, postfix logs `sent`, and the message may surface hours
  later or never. The app cannot tell "delivered", "delayed", and "dropped" apart.
  Tripwires are the bounce mailbox and occasional end-to-end delivery checks — and,
  for delays specifically, the `Received:` timestamp gap in whatever finally arrives
  (read it bottom-up; the largest gap names the culprit hop). This is exactly how the
  original problem hid — and why it looked like a drop when it was a delay.
- **Bounce mailbox.** Envelope sender is rewritten to `…@physics.uu.se`; NDRs follow
  `Return-Path`, so bounces land at `vald@physics.uu.se`. That mailbox needs a human
  reading it.
- **Two vald mailboxes.** `vald@physics.uu.se` (central Exchange, app bounces) vs
  local user `vald` on neon (`/var/mail/vald`, cron output).
- **GDPR footer.** The university appends a Swedish+English disclaimer to outbound
  mail, so delivered bodies are never exactly what the app wrote.

### Debugging entry points
```bash
sudo -u vald /home/vald/vald-www.git/bin/vald-manage test_email you@example.edu
journalctl -u vald --since -1h | grep -i 'Failed to send'          # app-side send failures
sudo journalctl -t postfix --since -1h | grep -Ei 'to=<|relay=|status=|dmarc|sasl|tls'
```
