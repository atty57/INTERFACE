# Fault injection in the mock core-banking app

Append `?fault=<name>` to any route. The name is stored in a cookie so it survives the
frameset's own navigations, which makes every fault reproducible by hand: open
`http://localhost:8000/?fault=dialog` in a browser, sign on, and search as normal.
`?fault=none` clears it.

Two faults clear themselves after firing once (`timeout`, `dialog_once`), so a bounded
recovery has something to succeed at. The rest are sticky, so a bounded recovery has
something to exhaust against.

| `fault=` | Condition | Where it fires | Sticky |
|---|---|---|---|
| `validation` | Validation error on a submitted form — "Enter a valid Member ID" | search submit | yes |
| `not_found` | Record not found — "No member found for that Member ID" | detail | yes |
| `permission_denied` | "You are not authorized to view this record" | detail | yes |
| `dialog` | Unexpected interstitial, `role="dialog"`, dismissable by its own Continue control — but it comes straight back | detail | yes |
| `dialog_once` | Same interstitial, but Continue really dismisses it | detail | no |
| `timeout` | Session cookie is genuinely invalidated; re-authentication is required | detail | no |
| `slow` | 3-second page load, to be waited out rather than failed | detail | yes |
| `ambiguous` | One page matching the success checkpoint *and* a business outcome at once | detail | yes |
| `no_names` | Accessible names (`title=`) stripped, forcing weaker targeting signals | search, detail | yes |

The first six are the runtime conditions the brief names. `ambiguous` and `no_names`
exist purely so classification and locator behaviour are testable; `dialog_once` exists
so recovery-succeeds and recovery-exhausts are both demonstrable.

Natural (fault-free) conditions the app also produces: member `99999` is unknown, member
`77777` is restricted, and a non-numeric Member ID fails validation.
