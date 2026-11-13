# Petal in Daisy

Petal is Daisy's own language model, trained from scratch on GitHub's free
runners and shown to visitors inside Daisy. She learns non-stop; you check in
and watch her grow.

```
GitHub Actions (every 6 h)            Your Daisy website
  harvest text  ->  train  ->  ──►  petal-data branch  ──►  petal_sync.py pulls it
  export weights + diary                                      /petal page + Petal window
```

## One-time setup (5 minutes)

1. **Add these files to the repo** (same paths) and push to `main`.
2. **Repo > Settings > Actions > General > Workflow permissions:** choose
   *Read and write permissions*. Save.
3. **Actions tab > "Petal learns" > Run workflow.** The first runs only collect
   text (she is "not born yet"). After about 2 MB of text she does her first
   lesson and appears on the site.
4. **On your web host**, add the environment variable
   `PETAL_REPO=your-github-name/your-repo-name` and redeploy.
   (Private repo? Also add `PETAL_GITHUB_TOKEN`, a token with read access.)
5. Open `https://your-site/petal`, or choose **Settings > Response model > Petal**
   inside Daisy, which opens her small window.

## What visitors see

- **Model picker:** Beni, Beni 2, **Petal**. Choosing Petal opens her window
  and also makes the main chat answer with her.
- **Talk to Petal:** her raw, unfiltered replies. Early on they are noise.
- **Her growth:** loss curve, "Day 1 vs today" answers to the same questions,
  and a log of every lesson.
- Share `https://your-site/petal` directly to show her off.

## Good to know

- **Public repo recommended.** Actions minutes are free for public repos.
  Private repos get a monthly allowance that continuous training uses up fast.
- **Read GitHub's Actions terms.** Long training jobs are heavy use of free
  runners. If you want the safest setup, train on your own PC (the
  `daisy-pc` package) and upload the results to the `petal-data` branch instead.
- **Scheduled workflows pause** after 60 days without activity on the repo;
  re-enable them in the Actions tab if that happens.
- **Size:** `tiny` (about 1.3M parameters) only. Bigger checkpoints exceed
  GitHub's 100 MB file limit.
- **Privacy:** real user conversations are NOT used for training
  (`PETAL_USE_CONVERSATIONS=1` turns that on; a public Petal could repeat
  what users typed, so leave it off). Petal chats are never logged.
- **Hosting:** the website needs only `numpy` extra (no PyTorch). Each
  visitor is limited to 12 messages a minute.
- **Expectations:** Petal is a real transformer language model, but tiny.
  Expect noise, then broken sentences, then Wikipedia-flavoured text with
  invented facts. She will not be ChatGPT. The honest note on her page says so.

## Settings (environment variables)

| Variable | Where | Meaning |
|---|---|---|
| `PETAL_REPO` | website | `owner/repo` to follow (turns sync on) |
| `PETAL_BRANCH` | website | default `petal-data` |
| `PETAL_GITHUB_TOKEN` | website | only for private repos |
| `PETAL_SYNC_MINUTES` | website | how often to check, default 20 |
| `PETAL_MAX_CORPUS_MB` | training | corpus cap, default 60 |
| `PETAL_USE_CONVERSATIONS` | training | `1` to include real chats (not advised) |
