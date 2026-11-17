"""
PETAL GPU RUNNER: one learning round on a free GPU (Kaggle or Colab)
====================================================================
Does exactly what the GitHub workflow does, but on a GPU:

  1. downloads the code (main) and Petal's memory (the petal-data branch)
  2. runs petal_cycle.py: harvest, tokenize, train on the GPU, export, diary
  3. publishes her back to the petal-data branch, which the website reads

Safety: if anyone else published to petal-data while this ran (for example the
GitHub schedule), it will NOT overwrite their work. It saves this round to a
backup branch instead and tells you.

Settings (environment variables, set by the notebook):
  PETAL_REPO         owner/name of your GitHub repo           (required)
  GITHUB_TOKEN       token with write access to that repo     (required)
  PETAL_HARVEST_MIN  minutes of reading before training       (default 20)
  PETAL_TRAIN_MIN    minutes of GPU training                  (default 150)
  PETAL_BATCH        examples per step                        (default 128)
"""

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = os.environ.get("PETAL_REPO", "").strip()
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
URL = os.environ.get("PETAL_GIT_URL") or f"https://github.com/{REPO}.git"
HARVEST = os.environ.get("PETAL_HARVEST_MIN", "20")
TRAIN = os.environ.get("PETAL_TRAIN_MIN", "150")
BATCH = os.environ.get("PETAL_BATCH", "128")
WORK = Path(os.environ.get("PETAL_WORK", "petal_gpu_work")).resolve()


def _auth():
    """Token goes in an HTTP header, never in a URL, so it can't leak into logs."""
    if TOKEN and URL.startswith("https://"):
        b = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
        return ["-c", f"http.extraheader=AUTHORIZATION: basic {b}"]
    return []


def git(*args, cwd=None, check=True, capture=False):
    try:
        return subprocess.run(["git", *_auth(), *args], cwd=cwd, check=check, text=True,
                              capture_output=capture)
    except subprocess.CalledProcessError as e:
        # Never let Python print the command: it contains the (base64) token.
        sys.exit(f"git {args[0]} failed (exit code {e.returncode}). Check PETAL_REPO, and that the "
                 f"token can read and write this repository.")


def remote_sha(branch):
    out = git("ls-remote", URL, f"refs/heads/{branch}", capture=True, check=False).stdout.strip()
    return out.split()[0] if out else None


def say(msg):
    print(f"[petal-gpu] {msg}", flush=True)


def gpu_ready():
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def main():
    if not (REPO or os.environ.get("PETAL_GIT_URL")) or (not TOKEN and URL.startswith("https://")):
        sys.exit("Set PETAL_REPO and GITHUB_TOKEN first (the notebook's first cell does this).")
    if not gpu_ready() and os.environ.get("PETAL_ALLOW_CPU") != "1":
        sys.exit("No GPU found. In the notebook settings turn Accelerator on (GPU), then run again.")
    say("GPU ready." if gpu_ready() else "No GPU (test mode).")

    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    code, data = WORK / "code", WORK / "petal_data"

    say("downloading the latest code")
    git("clone", "--depth", "1", URL, str(code))

    # Fail fast: make sure the token can WRITE before spending any GPU time.
    say("checking that the token can save to your repo")
    test = git("push", URL, "HEAD:refs/heads/petal-preflight", cwd=code, check=False, capture=True)
    if test.returncode != 0:
        sys.exit("GitHub refused a test write, so the token cannot save Petal. Fix the token: in GitHub > "
                 "Settings > Developer settings > Fine-grained tokens, edit it, select the repo, and set "
                 "Contents to 'Read and write'. Nothing was trained, so no GPU time was used.")
    git("push", URL, "--delete", "petal-preflight", cwd=code, check=False, capture=True)

    start_sha = remote_sha("petal-data")
    if start_sha:
        say("restoring Petal's memory")
        git("clone", "--depth", "1", "--branch", "petal-data", URL, str(data))
        shutil.rmtree(data / ".git", ignore_errors=True)
    else:
        say("no petal-data branch yet: starting fresh")
        data.mkdir()

    if os.environ.get("PETAL_SKIP_PIP") != "1":
        reqs = code / "petal-requirements.txt"
        if reqs.exists():
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(reqs)], check=False)

    say(f"Petal learns: {HARVEST} min reading, {TRAIN} min training, batch {BATCH}")
    env = {**os.environ, "PETAL_DATA_DIR": str(data)}
    subprocess.run([sys.executable, "petal_cycle.py", "--harvest-minutes", HARVEST,
                    "--train-minutes", TRAIN, "--batch", BATCH], cwd=code, env=env, check=True)

    say("publishing")
    git("init", "-q", "-b", "petal-data", cwd=data)
    git("config", "user.name", "petal-bot", cwd=data)
    git("config", "user.email", "petal-bot@users.noreply.github.com", cwd=data)
    git("add", "-A", cwd=data)
    git("commit", "-q", "-m", "Petal (GPU round)", cwd=data)

    end_sha = remote_sha("petal-data")
    if end_sha != start_sha:
        git("push", "-f", URL, "petal-data:petal-gpu-backup", cwd=data)
        say("Someone else published to petal-data while this ran (is the GitHub schedule still on?).")
        say("To protect their work, this round was saved to the branch 'petal-gpu-backup' instead.")
        sys.exit(2)
    git("push", "-f", URL, "petal-data", cwd=data)
    say("done. Her website picks this up within about 20 minutes.")


if __name__ == "__main__":
    main()
