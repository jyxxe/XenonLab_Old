# Account tree filtering — Issue 232

Accounts & API keys now has a named search field above the account tree. It matches
display names and login names without regard to case; whitespace around the query
is ignored. The count shows matching accounts against all loaded accounts. A clear
action restores the full tree, and an empty query result has an explicit message.

Filtering changes only the visible row list. Selection, unsaved account settings,
effective permissions, parent choices and API key ownership continue to use the
complete currently loaded authorized tree. A selected account may be hidden by
the filter while its details remain open. Clicking or using Enter on a matching
row uses the existing account selection flow. The original hierarchy indentation
is calculated from the full tree, including parents excluded by the query.

There is no new API, permission scope, account-data write or persistence. Typing
and clearing issue no requests. Account/key operations still use the existing
server authorization and account selection behavior.

## Local verification

From `apps/desktop-web`, run with an installed Chromium:

```sh
CHROME_BIN=/usr/bin/google-chrome node --test --test-concurrency=1 tests/browser-acceptance-account-filter.test.mjs tests/browser-acceptance-account-patch.test.mjs
npm test
npm run build
```

The new browser test mounts the production `Accounts` component with 61 authorized
synthetic accounts. It verifies name/login case matching, Chinese display names,
empty/clear behavior, unchanged draft/permissions/key context and loaded data,
no requests when typing, and explicit keyboard selection of a result. The fixture
rejects every write request. Existing account-edit acceptance still covers
unchanged grants and explicit removal of revoked grants.

Evidence is written to `runtime/verification/issue232`. An optional
`ACCOUNT_FILTER_NEGATIVE_CONTROL=1` run asserts the original absence of a filter;
it is a reproduction mode for the baseline and is not normal acceptance.

These are local synthetic account fixtures, not production identity-chain or
account-size evidence. No service dependency or migration is introduced.
