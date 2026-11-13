You are reviewing one finished task of the Homesh project, unattended. Another
model built it. Your job is to find what is wrong with it before the next task
is built on top, and to fix what you find.

TASK: {ID} - {TITLE}
DESIGN: docs/design/{DOC}
COMMITS: {RANGE}   (see them with `git log --stat {RANGE}` and `git diff {RANGE}`)

Read CLAUDE.md, docs/design/README.md and docs/design/{DOC}, then the diff.

Check, in this order, and stop at nothing you find -- list all of it:

1. Privacy of the repository: anything in the diff or the commit messages that
   describes a real household (README section 2.2).
2. Access: every new route takes a signed-in user; every route returning
   library rows, rooms, faces or names applies the caller's scope inside the
   query and per row; an item outside the scope answers 404. Look for a count,
   a suggestion or an error message that leaks existence.
3. Secrets: nothing secret in a table, a log line, an exception or a response.
4. The rest of README sections 2.3 to 2.6, rule by rule.
5. The design: is everything the task's section asks for built, and nothing
   that belongs to another task? Are the tests the design lists all present,
   and do they test what they claim (a test that cannot fail is a finding)?
6. Correctness: read the new code for bugs.

Then:

- Fix every finding yourself, with tests, in new commits (never amend, never
  force-push). Run `.\tools\run-tests.ps1`, push, and run
  `.\tools\verify-ci.ps1` until it prints "CI is green.".
- Do not start, stop, rebuild or deploy anything, do not read .env, and do not
  pass --no-verify.
- Write a short report to .local/build/review/{ID}.md: what you found, what you
  fixed, and anything you could not fix.

Your final message must end with exactly one of these lines:

VERDICT: PASS
VERDICT: FAIL <one sentence: what is still wrong and could not be fixed here>

`VERDICT: PASS` means: you would build the next task on this, the working tree
is clean, and CI is green on the last commit.
