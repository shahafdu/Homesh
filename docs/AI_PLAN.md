# The AI features — the plan

Agreed with Shahaf in October 2026, **not started.** This is the plan to pick up
when the work begins; `AI.md` describes what is already built and the rules it
follows. Where the two disagree, this file is the newer decision and `AI.md` is
updated when the work lands.

Nothing here changes the rules in `AI.md`: the AI acts only through the API as
the person asking, spending is refused by the platform rather than avoided by the
code, and what is private stays in the house.

---

## The shape of it

Three kinds of model, each doing only what it is good at:

| What | Where it runs | What it sees |
|---|---|---|
| **Embeddings** — EmbeddingGemma 2: photos, video frames, sound, documents, and the question, all in one vector space | **Here**, on the PC | Everything, and nothing leaves |
| **Faces** — a dedicated face model (below) | **Here**, on the PC | Faces only, and nothing leaves |
| **Speech to text** — Whisper, a Hebrew-tuned build | **Here**, on the PC | The spoken command; the audio is discarded |
| **Chat and actions** — Gemini Flash, OpenRouter free models | A provider | The question as text, and the *rows* that matched: filenames, dates, tags. Never a photo, a face or a voice |

**The chat model never reads embeddings.** A vector means something only to the
model that made it. The question is embedded here, Postgres finds the nearest
vectors — after the access rules have removed what the asker may not see — and
the chat model is handed the matching rows as text. Plain search needs no chat
model at all: it is fast, free, works offline and works on the standby.

---

## 1. Embeddings — EmbeddingGemma 2

Google's open-weight multimodal embedding model (Apache 2.0, about 740M
parameters, released October 2026). Text, images, video frames and audio map to
one space, so one model replaces the separate CLIP model planned earlier.
Vectors are 768-dimensional and can be truncated (Matryoshka) to 128-512; at 256
the whole library is roughly 150 MB in Postgres.

**Stored in the catalog, computed once.** Vectors live beside the item, so search
works with the RAID off and on the Oracle standby. The standby needs only the
text encoder (about 200 MB) to embed a question — within its 6 GB.

**Order of work:** photos, then video (below), then documents, then music audio.
Music audio last because the earlier decision stands: a language model given
`Artist — Title` already knows the song. Audio vectors earn their keep for
"sounds like this" and for recordings no model has heard of.

**Unknowns, settled by the first spike (section 9):** whether Hebrew questions
retrieve well (Google's announcement does not name Hebrew — the fallback is to
translate the question to English first, one cheap call); throughput on four
efficiency cores, which Google has not published; and how it runs from a Python
server, since the announced runtimes are LiteRT and MediaPipe.

## 2. Video — the clips scattered among the photos

Measured on the library in October 2026:

| Where | Clips | Total | Median clip | Size |
|---|---|---|---|---|
| In folders that also hold photos | 6,313 | 90 hours | 28 s | 686 GB |
| Elsewhere | 874 | 68 hours | 13 s | 111 GB |

Short phone clips, uncategorised. Each is treated as **a handful of photos plus a
sound track**:

- **Frames** — one about every five seconds plus one at each scene change, at
  most about 60 per clip: roughly 65,000 frames, two-thirds of the photo job.
  Each frame's vector keeps its timestamp, so a result opens the clip *at that
  moment*.
- **Sound** — EmbeddingGemma 2 embeds audio too, in roughly 10-second chunks
  (about 32,000). Singing, the sea, a dog: findable without transcription.
- **Reading** — ffmpeg seeks to the frames it needs and reads only those bytes,
  which matters for files on Drive.
- **Sorting** — with frames and sound embedded, the AI can *suggest* an event and
  a date for a loose clip, stored as `origin='ai'` beside the file's own tags.
  It never moves or renames anything.

Faces in video come from the same frames (section 3).

## 3. Faces — people that matter, tagged by hand where the model is wrong

**What it is for:** finding specific people — close family and friends — and
"more photos of this person" from a face in the photo on screen. The thousands
of other people in the library are detected and grouped but never need a name,
and stay out of the way.

**A different model from the embeddings.** EmbeddingGemma groups photos that
*look* alike — beach, birthday cake — not photos of the same person. Faces need
a detector and a face-recognition model. Candidates to compare in the spike:

- **YuNet + SFace** (OpenCV, Apache 2.0) — small, fast on a CPU, licence-clean.
  The baseline.
- **InsightFace / ArcFace** — more accurate, especially across ages, but the
  pretrained weights are licensed for non-commercial use. Acceptable for a
  household; never bundled with this repository — downloaded at install, like
  the other models.

### People change over time

The model is never trusted with that alone. Three things together:

1. **A person is many faces, not one average.** Every confirmed face is kept as
   an example, and a new face is compared with its nearest examples rather than
   with a single centre — so a person at six and at forty are both "them",
   connected by the photos in between.
2. **The date helps.** A face is compared first with that person's examples from
   around the same years, from the photo's capture date.
3. **Uncertain matches are suggestions, never facts.** Below a threshold the
   app says *"might be Shahaf"* and waits for a yes or no.

### You correct it, and corrections are permanent

Google Photos both splits one person into several and merges two people into
one. Both are expected here too, so correcting is a first-class feature:

- **This face is this person** — a confirmation, `origin='user'`.
- **This face is *not* this person** — a rejection, kept as a hard rule: no
  later run may suggest it again.
- **Merge** two groups that are one person; **split** a group that is two.
- **Hide** a group you do not care about.

Confirmations and rejections are never overwritten by a re-run, the same rule as
every other piece of metadata: `origin` says who decided, and a person outranks
the model.

### Data

- `faces` — item, frame time for video, box, face vector, quality.
- `people` — name, and who may see the name.
- `face_labels` — face, person, `is` / `is_not`, `origin`, who, when.
- Groups of unnamed faces are computed, not stored as truth.

**Biometric data stays in the house.** Face vectors are never sent to a provider.
They do reach the standby with the rest of the catalog, inside the encrypted
backup — the one decision to revisit when the work starts: carry them to Oracle
(face search works with the PC off) or leave them out (it does not).

**Who can name people:** anyone, on photos they can already see. Every change is
in the audit log (section 6).

## 4. Models — Gemini and OpenRouter, each for what it is good at

Each **job** has an ordered list of models. When the first hits its free limit,
the next takes over.

| Job | First | Fallback |
|---|---|---|
| Chat and commands — calls the API | **Gemini Flash** — the strongest at tool calls, good Hebrew | OpenRouter free models with tool support |
| Bulk background — music mood and genre, event suggestions, document summaries | **OpenRouter free models**, so they never spend Gemini's daily quota | Another OpenRouter model; Gemini at night |
| Translating a Hebrew search into English (only if the spike says it is needed) | Whichever is fastest | — |
| Embeddings, faces, speech | **Local only** | — |

So a 95k-token tagging run never makes the chat wait, and running out of one
free quota moves work along rather than stopping it.

**Free means someone else's terms.** On Gemini's free tier, Google may use
requests to improve its products; on OpenRouter many free models require
allowing the provider to train on prompts (an account setting). What is sent is
the question and matching rows — never a photo, face or voice. Accepted, October
2026. The paid tiers remain available behind the existing monthly cap.

**A Google project with no billing account cannot be charged at all** — the same
principle as never upgrading Oracle.

**Keys stay in `.env`.** Never in the database, so never in a backup or on the
standby. The page below says whether each key is present and working, never what
it is.

### Settings → AI (administrators only)

- **A catalogue of the free models, refreshed daily.** OpenRouter publishes every
  model with its price and abilities — tool calls, images, context length — so
  the free ones are found, not hand-listed. Google's model list carries no
  prices, so Gemini models are listed with a **Test** button that checks whether
  the free tier answers.
- **Per job, the ordered list**, chosen from the catalogue.
- **Warnings** when a chosen model vanishes or starts charging — free models come
  and go — with the fallback taking over meanwhile.
- **Paid models hidden** while the monthly cap is zero.
- **Usage** — calls per model today against its limit, and fallbacks taken.

Stored in the database, so the choices travel with the backups.

## 5. Background work — weeks, at night, without taking the PC

Embedding, faces and tagging together are weeks of work on this machine. They
run so that the PC stays usable for everything else on it:

- **Its own container**, a worker separate from the server, with hard Docker
  limits on CPU and memory, and the lowest scheduling priority inside them.
- **A night window**, set in Settings → AI, default 01:00-07:00. Outside it the
  worker sleeps — except for small jobs a person is waiting on, such as embedding
  a question.
- **Pause now / resume** in the same page.
- **A resumable queue** in the database: every item's progress is recorded, so
  stopping — a restart, a pause, the drive switched off — loses nothing.
- **Only while the bytes are there.** An item on a drive that is switched off
  waits for it; nothing is marked done that was not done.
- **Progress** on the page: done, waiting, failed, and an estimate.

### A GPU, or another machine

The worker takes jobs from the queue, so it can run anywhere that reaches the
server — including the laptop. Whether its twelve-year-old GPU helps is a
question for the spike rather than a guess: GPUs of that generation (Maxwell) are
being dropped by current CUDA, so the likely route is Vulkan through
whisper.cpp / llama.cpp, or simply the laptop's CPU — four full-speed cores
may outrun four efficiency cores without the GPU at all. Measured, then decided.

## 6. History and audit — everything the AI was asked and did

A change from the earlier rule that the record holds "never the question and
never the answer": you want your questions back, and a searchable account of
what was done.

**Your history — what you asked.**
- Every question, typed or spoken (the transcript; never the audio), with its
  time and what it led to.
- **Repeat** from history in one tap — asked again, answered fresh.
- **Search** your history.
- **Clear** it — one entry, or all. Yours only; nobody else's.

**The audit log — what the AI did.**
- Every action: which tool, with which arguments, as whom, what it changed —
  "created playlist *Mellow evening*, 23 tracks" with a link to it — and what was
  refused and why.
- **Searchable**, in Hebrew and English (Postgres has no Hebrew stemmer, so
  trigram matching rather than language-aware search).
- **Kept when history is cleared.** Clearing what you asked does not erase the
  record of what changed in the house.
- **Readable by me**: an administrator's export of the log, so "review what the
  AI did last week" is a question you can hand over.

**Who sees what:** your own questions are yours. Administrators see the audit
log for everyone — what changed — but not the wording of someone else's
questions.

## 7. Voice — Whisper, for mixed Hebrew and English

Push-to-talk in the web app and the phone app; the recording goes to the PC,
becomes text, and is discarded. Stock Whisper handles Hebrew but stumbles when a
sentence switches language, so two Hebrew-tuned builds are compared on real
recordings:

- **ivrit.ai's Whisper large-v3-turbo** (faster-whisper) — the strongest Hebrew.
- **Whisper-Hebrish** — tuned for English speech with Hebrew words in it.

A five-second command on four efficiency cores should take a few seconds — fine
for push-to-talk. No wake word.

## 8. Tagging

One pass over the music: mood and genre from `Artist — Title`, about 95k tokens,
on the bulk-work models. Then event suggestions for loose clips (section 2).
All of it `origin='ai'`, beside the file's own tags, never instead of them.

---

## 9. Order of work

1. **Spike, about a day — decides everything after it.**
   - EmbeddingGemma 2: Hebrew and English questions against 500 photos and 50
     clips; throughput per photo and per clip here; how it runs from Python.
   - Faces: YuNet + SFace against InsightFace on a few hundred family photos,
     including the same people as children.
   - Whisper: ivrit.ai against Whisper-Hebrish on a few recordings of yours.
   - The laptop: CPU and GPU against this PC, if it is to hand.
2. **Settings → AI**, the model catalogue, per-job lists, Gemini beside
   OpenRouter.
3. **The background worker** — queue, night window, limits, pause, progress.
4. **Embeddings** — photos, then video frames and sound, then documents. Then
   the search box.
5. **Faces** — detection and grouping, then naming and corrections, then "more
   of this person".
6. **History and audit** — before the AI is given anything that changes data.
7. **Chat and commands** with Gemini.
8. **Voice.**
9. **Tagging** — music, then clip suggestions.

## 10. Later — when better free tools or hardware arrive

Deliberately not in the plan above; worth revisiting when something changes:

- **Speech in home videos** — find clips with speech, transcribe only those,
  overnight over weeks. Optional; nothing above depends on it. A GPU makes it
  realistic.
- **A GPU** for all of the above — re-measure the laptop, or any newer card.
- **Captions for photos by a vision model** — would send the photo out, so only
  ever on an explicit request, never in bulk.
- **A wake word.**
- **Newer free models** — the catalogue (section 4) picks them up as they appear.
