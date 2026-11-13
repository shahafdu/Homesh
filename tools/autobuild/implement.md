You are building one task of the Homesh project, unattended. Nobody is at the
keyboard: you cannot ask a question and wait. You either finish the task, or
you write down what you need and stop.

TASK: {ID} - {TITLE}
DESIGN: docs/design/{DOC}

Do this, in order:

1. Read CLAUDE.md, then docs/design/README.md in full, then docs/design/{DOC}.
   Build exactly the part named in the task title; other sections of that file
   belong to other tasks.
2. Read the existing code the design names before changing it. If the code no
   longer matches what the design says exists, that is a reason to stop (see
   "Asking for help").
3. Build it: migration, server code, tests, web or Android code, and the
   documentation changes the README lists.
4. Run the tests: `.\tools\run-tests.ps1`. Fix what fails.
5. Commit with a message that explains why. Push to main. Run
   `.\tools\verify-ci.ps1` and wait for it. If CI is red, fix it and repeat.
6. Leave the working tree clean.

Hard limits, in addition to the README's rules:

- Do not start, stop, rebuild or deploy the running server or the standby, and
  do not publish an Android release. The owner does those afterwards.
- Do not read or edit .env. If the task adds a variable, add it to
  .env.example with a one-line ASCII comment; the owner sets the real value.
- Do not edit .local/private-words.txt or the git hooks, and never pass
  --no-verify. A hook that refuses your commit is telling you something is
  wrong with the content.
- Do not touch a task other than this one, and do not "improve" unrelated code.
- No real name, address, room, file name or count from this household in any
  tracked file or commit message.

{EXTRA}

Asking for help. If you reach one of the stop conditions in README section 2.7,
or you need a decision, a key, a file or a device only the owner has:

- undo any half-made change so the working tree is clean (commits already
  pushed and green may stay);
- write the question to .local/build/help/{ID}.md: what you were doing, what
  stopped you, and exactly what you need, as a short list the owner can answer
  line by line. No secrets and no file contents in it;
- end with the line `RESULT: HELP`.

Your final message must end with exactly one of these lines:

RESULT: DONE
RESULT: HELP
RESULT: FAILED <one sentence saying why>

Write `RESULT: DONE` only when the commits are pushed and verify-ci.ps1 has
printed "CI is green." for the last of them.
