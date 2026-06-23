# Smoke Test With Real Data

Use this checklist before closing any `task_type='execute'` task that touches a critical engine:

- booking
- payment
- voice routing
- availability

The smoke is not valid with fabricated records only. Use real persisted records from multiple channels and keep the SQL plus execution evidence in the task closure.

## 1. Scope

- Critical engine:
- Code path / command / endpoint under test:
- Environment:
- Version / commit:
- Operator-facing risk if this fails:

## 2. Real Record Discovery

List the SQL used to find 3-5 real records across different channels. Prefer records created by real production/customer flows; do not use synthetic fixtures unless a channel has no real record and the reason is documented.

Required channel coverage:

- web:
- voice:
- whatsapp:
- admin:

Example shape:

```sql
-- Replace table/column names with the live schema.
SELECT id, channel, starts_at, ends_at, status, created_at
FROM bookings
WHERE channel IN ('web', 'voice', 'whatsapp', 'admin')
  AND status NOT IN ('cancelled', 'deleted')
ORDER BY created_at DESC
LIMIT 20;
```

Selected records:

| # | Channel | Table | Record ID | Why This Record Matters |
|---|---|---|---|---|
| 1 | web |  |  |  |
| 2 | voice |  |  |  |
| 3 | whatsapp |  |  |  |
| 4 | admin |  |  |  |
| 5 | other |  |  |  |

## 3. Engine Execution

Run the target engine against each selected record and capture the exact command, request, or tool call.

| Record ID | Channel | Command / Endpoint | Expected Result | Actual Result | Pass |
|---|---|---|---|---|---|
|  | web |  |  |  |  |
|  | voice |  |  |  |  |
|  | whatsapp |  |  |  |  |
|  | admin |  |  |  |  |

## 4. Boundary Case

Include at least one real or deliberately constructed boundary case that crosses an edge. If no real boundary record exists, create the minimum safe test record and mark it as synthetic-boundary, not as production evidence.

Required edge type, choose at least one:

- straddle: record overlaps the tested slot boundary
- midnight: record crosses 00:00 local time
- year boundary: record crosses Dec 31 / Jan 1

Boundary evidence:

| Edge Type | Table | Record ID | Input Window | Expected Result | Actual Result | Pass |
|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |

## 5. Closure Checklist

- [ ] SQL for discovery is included.
- [ ] 3-5 real records are listed.
- [ ] Records cover at least three different channels.
- [ ] Web, voice, whatsapp, and admin were checked or an explicit unavailable-channel reason is documented.
- [ ] The engine ran against every selected record.
- [ ] The boundary case covers straddle, midnight, or year boundary.
- [ ] The closure states whether any synthetic-boundary record was used.
- [ ] The task is not closed as fixed until all failures have either been fixed or recorded as followups with IDs.
