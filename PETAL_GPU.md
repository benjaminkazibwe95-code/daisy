# Petal on a free GPU (Kaggle)

A free GPU trains Petal many times faster than GitHub's 2-core computers.
Kaggle gives about 30 GPU hours a week (the exact number is shown in your
account and can change) with sessions of up to 12 hours.

## One-time setup

### 1. Make a GitHub token (so the notebook can save Petal)
GitHub > your profile picture > **Settings** > **Developer settings** >
**Personal access tokens** > **Fine-grained tokens** > **Generate new token**.

- Name: `petal-gpu`. Expiration: 90 days (you will renew it then).
- Repository access: **Only select repositories** > choose `daisy`.
- Permissions > Repository permissions > **Contents: Read and write**.
- Generate, then **copy the token now** (GitHub shows it once).

### 2. Turn off the GitHub schedule (so two trainers don't collide)
Repo > **Actions** > **Petal learns** > the **...** menu > **Disable workflow**.
(If both run, the GPU round is saved to a backup branch instead of overwriting
the other, so nothing is lost, but one round of training is wasted.)

### 3. Set up Kaggle
1. Sign in at kaggle.com. Kaggle asks you to verify your phone number before
   it lets you use a GPU and the internet (Settings > Phone verification).
2. **New Notebook** > **File** > **Import Notebook** > upload `petal_on_kaggle.ipynb`.
3. In the notebook settings (right panel or **Session options**):
   - **Accelerator:** GPU (T4 or P100)
   - **Internet:** On
4. **Add-ons** > **Secrets** > **Add secret**: label `GITHUB_TOKEN`, value = the
   token from step 1. Make sure it is attached to this notebook.

### 4. First test (about 40 minutes)
In the notebook's code cell change `TRAIN_MIN = "150"` to `"15"` and press **Run All**.
At the end you should see `done. Her website picks this up within about 20 minutes.`
Then open her page and check the diary for a new entry.

## Real runs

Set `TRAIN_MIN` back to `"150"`. Then **Save Version > Save & Run All (Commit)**,
which keeps running on Kaggle's servers even if you close the app.

One round is about 20 min of reading + the training time + a few minutes to
prepare and publish: roughly 3 hours at 150 minutes. With about 30 GPU hours a
week, one round a day fits comfortably. After one good manual run, look in the
notebook's menu for a **Schedule** option to repeat it by itself (Kaggle has
moved this menu around over time).

## If something goes wrong

| Message | Meaning |
|---|---|
| `No GPU found` | Turn Accelerator on in the notebook settings, run again. |
| `Add a Secret named GITHUB_TOKEN` | The secret is missing or not attached to the notebook. |
| `Could not download your repo` | Wrong `REPO` name, or the token cannot read the repo. |
| `git push failed` | The token needs **Contents: Read and write**. |
| `Someone else published ... backup branch` | The GitHub schedule is still on. Disable it (step 2). |

The token is never printed. Keep the notebook **private** anyway, and never
paste the token into a cell.

## Be realistic

A GPU makes her finish her lessons fast, but her current body is tiny
(about 1.3 million parameters). On the same reading she will soon reach
that body's ceiling, and more hours won't push her past it. The next real
step after this works is a bigger body and more reading (bulk datasets
instead of crawling one page a second). The GPU is what makes that possible.
