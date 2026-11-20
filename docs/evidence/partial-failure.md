# Reminders down on Azure, and a deploy that failed and was fixed

On 2026-11-20 the reminders service was broken on purpose on Azure, to see what a person sees while one service is down (ADR-6) and to check that the pipeline refuses to call a broken deploy a success (ADR-9). Nothing in the code was changed to cause it. All times are UTC.

## 1. Breaking reminders (09:51)

The laptop's address was allowed through the SQL firewall for this, and the rule was removed at the end. As the server admin, in `crintz-ieu2024-reminders-db`:

```sql
DENY CONNECT TO reminders;   -- 09:51:13
```

The service's own login could no longer open its database. Right after, `/health`:

```
reminders  HTTP 503  {"status":"error","db":"down","version":"0669aa1f8da1f34958300b090b2f479695be6498"}
inbox      HTTP 200  {"status":"ok","db":"ok","version":"0669aa1f8da1f34958300b090b2f479695be6498"}
```

## 2. What a person saw

[partial-failure-reminders-down.png](partial-failure-reminders-down.png): a new test account on the live site saved "remind me to call the dentist" with a time (23 Nov, 10:00).

- The line was saved. Saving took 4.5 s: inbox waits at most 2 s for reminders when it stores the time, and 2 s again when it lists the page.
- The line shows "no time yet, reminders is unavailable", and the red banner says "Reminders are unavailable right now. Lines still save; their times show again when reminders is back."
- The other lines, their folders and their tags showed as usual, because they only need inbox.
- The grey "starting up" banner also appeared. The UI treats a 503 from `/health` as a cold start (ADR-8), and here it cannot tell a database that is resuming from one that refuses the login. So it said Noteezer "carries on by itself", which was not true this time. It was left as is, because a real outage is far rarer than a cold start.

Inbox's log for that save, with the request crossing the seam:

```
2026-11-20 09:51:36,310 WARNING app.reminders_client Reminders did not answer PUT /reminders/22: The read operation timed out
2026-11-20 09:51:36,310 WARNING app.inbox Item 22 saved without its time: reminders is unavailable
2026-11-20 09:51:38,423 WARNING app.reminders_client Reminders did not answer GET /reminders: The read operation timed out
```

Reminders' log at the same time:

```
2026-11-20 09:51:35,681 INFO reminders.requests 1b87ac859675 GET /health 503 39ms
2026-11-20 09:51:36,472 WARNING app.db Database not available yet (attempt 1 of 6), retrying in 2 s
2026-11-20 09:51:38,508 WARNING app.db Database not available yet (attempt 2 of 6), retrying in 4 s
...
pymssql.exceptions.OperationalError: (18456, b'DB-Lib error message 20018, severity 14:\nGeneral SQL Server error: Check messages from the SQL Server\nDB-Lib error message 20002, severity 9:\nAdaptive Server connection failed (...)\n')
```

**Bug found:** the refused login (error 18456) was retried for about a minute on every request, as if the database were paused. pymssql's text for it also contains DB-Lib's "20002", and `db.py` looked for the cold-start codes anywhere in the text. Inbox's 2 s timeout kept the pages quick, but reminders held every request for a minute. Fixed in commit 8244179: the error number is checked first, and a test uses this exact text and fails on the old code.

## 3. The deploy that failed (09:53 to 10:23)

PR #17 (ADR-10 and the diagrams) was merged while reminders was still locked out. That started [CD run 44714488970, attempt 1](https://github.com/christophrrrrr/Noteezer/actions/runs/44714488970/attempts/1):

- CI, both image pushes, and the deploy steps all passed.
- The health check found inbox healthy on the new commit at the first try:
  ```
  inbox, attempt 1: {"status":"ok","db":"ok","version":"3eb51d68a4552cde10762a1f8d73cc8eb5941b2b"}
  ```
- Reminders' new revision never became ready, because its startup migration could not log in:
  ```
  curl: (28) Operation timed out after 30002 milliseconds with 0 bytes received
  reminders, attempt 40:
  ##[error]reminders never became healthy at https://crintz-ieu2024-reminders.calmtree-7bb96708.germanywestcentral.azurecontainerapps.io/health
  ```
- The run failed at 10:23:21.

**Second finding:** the check was meant to give up after about seven minutes, but it counted attempts. Each attempt could wait 30 s for curl plus 10 s, so the run took 26.7 minutes to fail. `cd.yml` now gives both services one shared 8-minute deadline.

## 4. The fix (10:23 to 10:26)

```sql
GRANT CONNECT TO reminders;   -- 10:23:57
```

Reminders answered `{"status":"ok","db":"ok","version":"3eb51d6..."}` at 10:24:02, without a restart. The failed job was re-run, and [attempt 2](https://github.com/christophrrrrr/Noteezer/actions/runs/44714488970/attempts/2) passed at 10:26:30:

```
inbox, attempt 3: {"status":"ok","db":"ok","version":"3eb51d68a4552cde10762a1f8d73cc8eb5941b2b"}
reminders, attempt 1: {"status":"ok","db":"ok","version":"3eb51d68a4552cde10762a1f8d73cc8eb5941b2b"}
Live at https://crintzieu2024ui.z1.web.core.windows.net
```

The firewall rule for the laptop was deleted, so only `AllowAzureServices` remains.

[partial-failure-after.png](partial-failure-after.png) shows the same account afterwards. The banner is gone, the line is still there, and the time typed while reminders was down is lost. The chip asks to pick it again under ⋯ Edit, as ADR-6 says it would.

The two fixes found by this test (the retry and the deadline) were merged in the next pull request, and its own deploy is the run that shows them working.
