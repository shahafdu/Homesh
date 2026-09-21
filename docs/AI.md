# The AI, and what it is allowed to do

Homesh can ask a language model things — what a song is like, what a photograph
shows, what you meant by a search. This document is the part that matters before
any of that: who pays, what it may touch, and what it cannot do.

The decisions here were settled in conversation before the code existed. They are
restated as rules because each one is a thing the implementation must not quietly
undo.

---

## The provider is configuration

```
AI_PROVIDER=openrouter
AI_API_KEY=<your key>
AI_MODEL=openai/gpt-oss-20b:free
AI_MONTHLY_CAP_MICROS=0
```

**OpenRouter** first: one key reaches many models, the free tier is genuinely
free, and every answer comes back with what it cost — which is what lets a limit
be arithmetic rather than a hope. **gpt-oss** is the model, on its free tier by
default.

Empty `AI_PROVIDER` means the feature is **absent**, not broken: nothing offers
it and nothing fails.

**The key lives in `.env` and nowhere else.** It is never returned by an
endpoint, never logged, and never written to a table — `/api/ai/status` says
whether a key is present, never what it is. The commit and push guards refuse
anything shaped like one, so it cannot reach the repository by accident.

## Spending is refused by the platform, not avoided by the code

Software that calls a paid API on a schedule is software that can bill on its own
when it has a bug. So the refusal is arithmetic, and it happens before the call:

- **A paid model is refused outright while the cap is zero**, which it is until
  somebody sets it. There is no path on a fresh install that spends anything.
- Above zero, the month's spend is **summed from `ai_calls` and checked before
  each call**. A budget alert reports after the fact; this refuses beforehand.
- **Spending is granted per account** (`users.may_spend_on_ai`; the owner always
  has it). Everyone else in the household can use the free model and cannot run
  up a bill.
- Costs are stored in **millionths** of a unit of currency. A request to a free
  model is 0 and a paid one is a fraction of a cent; neither is representable in
  whole cents, and floating point has no business counting money.

## Every attempt is on the record

`ai_calls` holds one row per attempt — **including every refusal**, which is the
interesting entry, since a refusal says the cap held or that somebody asked for
something they may not have. Each row is who asked, what for, which model, how
many tokens, how much it cost and how long it took.

**Never the question and never the answer.** It is an account of the work, not a
copy of the content. `/api/ai/history` reads it back, administrators only,
because it names who asked for what across the whole household.

## The AI holds no privileges of its own

It calls the same authenticated API the interface calls, as the person who asked,
so scope is enforced by the code that already enforces it rather than by the
model behaving well. Concretely, it **cannot**:

- add or remove rooms;
- change anybody's permissions;
- act beyond the asking person's own access;
- delete anything without a confirmation;
- send anything out of the house without somebody pressing something.

`server/app/ai.py` reaches neither the catalog nor the API: a caller hands it
text and gets text back. Whatever acts on the answer does so as the user.

## What is cached, and why that makes it cheap

Judgements land in `item_metadata` with `origin='ai'`, beside the file's own tags
and never overwriting them. Embeddings and earlier answers are cached the same
way. Later questions filter locally first and only ask about what is not known
yet — which also means it keeps working offline once warm.

## Spoken commands stay in the house

The words are turned into text **here**, by whisper.cpp on the PC, and never sent
to a provider. That is a deliberate split: what you *said* is the most private
thing in the whole feature, and the model that acts on it only ever needs the
text.

It is also the one speech job this machine can do. Transcribing a video library
on four efficiency cores is far too slow to be worth starting; a five-second
"play something mellow in the kitchen" is a fraction of a second. So speech is
on demand, never bulk.

## What is built

- ✅ The provider layer, the caps, the permission, and the record
  (`server/app/ai.py`, migration 026).
- ✅ `/api/ai/status` — whether it is on, which model, and for an administrator
  what has been spent this month.
- ✅ `/api/ai/history` — what it has been asked to do.
- ⬜ Whisper on the PC for spoken commands, and the tagging pass over the
  library; then the questions that use them: commands, finding things, content
  search. Each one calls the layer above rather than a provider.
