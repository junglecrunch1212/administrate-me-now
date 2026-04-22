# The AdministrateMe Field Manual

**For people who have never coded, who have a family depending on the thing working, and who want to improve it without breaking it.**

---

## How to read this guide

Ten chapters. Read them in order. Each one builds on the last.

If you skip ahead, you will get confused, and when you get confused, you will either stop (and never get the benefit) or guess (and break something). The order matters more than the speed.

Chapter 1 is the most important. If you get chapter 1 right, everything else is mechanics. If you get chapter 1 wrong, no amount of mechanics will save you.

**Time estimate:** 90 minutes to read cover to cover. Another 2–3 hours to set up what it describes. Then it becomes a reference you come back to when you forget a step.

---

## Chapter 1: What you're actually doing

Stop thinking of this as "coding."

You're not going to learn to code. You don't need to. What you're going to do is **direct an AI coder**, and then **save the good stuff**, and **be able to go back when something goes wrong**. That's the entire job.

There are exactly four activities in your new role. Everything else is mechanics that support one of these four:

### The four activities

**1. Direct.** You tell Claude Code what you want. "Add a feature that flags grocery items over $10 as anomalies." "The morning digest is too long on Mondays; make it shorter when there are more than 5 items." "The reward toast when I complete a task should have a celebratory sound option." These are the conversations you'll have. Claude Code writes the code. You don't.

**2. Review.** Claude Code shows you what it changed. You look at it. You don't need to read the code line by line. You need to look at two things: (a) did it change the thing I asked about, or did it also change a bunch of other stuff I didn't ask about? and (b) when I test it, does it do what I wanted? If yes to both, keep it. If no, ask Claude Code to fix it or throw the change away.

**3. Save.** When you have a change that works, you *commit* it. Commit is just a save button with a note attached — "I saved this; here's what it does." Your history is a list of commits. Each one is a checkpoint you can come back to.

**4. Rewind.** When something goes wrong — and things will go wrong — you go back to the last commit that worked. This is the superpower of this whole setup: **you can always go back.** Not "sort of go back if you remember what you changed." Actually go back, with one command, exactly.

Git and GitHub are the tools that make those four activities possible. Forks, branches, pull requests, merges — all of that is vocabulary for talking about directing, reviewing, saving, and rewinding. If you remember the four activities, the vocabulary will make sense when you meet it. If you try to memorize the vocabulary first, it will be a word salad.

### What this guide is NOT teaching you

- **To read code.** You might pick some up by osmosis; that's fine. But you won't learn to read code from this guide, and you don't need to.
- **To debug.** When something's broken, you'll tell Claude Code "this is broken, here's what I see, fix it." You're not the debugger; Claude Code is.
- **Software engineering practices.** Professionals have elaborate rituals (code review, CI/CD, design docs, unit test coverage targets). You don't need any of them. You need the four activities and the three disciplines in chapter 2.

### The mental shift

Stop reading about coding and start thinking about **operating a household system**. The Mac Mini in your closet is a piece of household infrastructure, like the HVAC or the router. You don't know how an HVAC works internally either — you know how to tell it to do something (thermostat), how to tell when it's broken (it's 90 degrees in the house), and when to call for help. AdministrateMe is the same, except the "thermostat" is Claude Code and the "call for help" is asking Claude Code what's wrong.

That mental shift — from "I'm learning to code" to "I'm operating a household system that I sometimes improve via an AI collaborator" — is worth more than any technical knowledge I can hand you.

---

## Chapter 2: The three disciplines that prevent disaster

Before any mechanics, learn three disciplines. If you break one of these, you will eventually have a bad day. If you hold all three, you will almost never have a bad day.

### Discipline 1: Experiment in the lab, deploy to the family.

The **lab instance** is AdministrateMe running on your MacBook with fake data. Experiments happen here. Ideas get tested here. When Claude Code writes something new, you run it here first. Breaking the lab is free. Nothing the family depends on lives here.

The **family instance** is AdministrateMe running on the Mac Mini with real data — real Apple ID, real Google, real Plaid, real iMessage, real family. The family instance only ever receives code that has already worked in the lab.

**The rule: you never, ever, ever tell Claude Code to change the family instance's code.** The family instance only gets changes by pulling them from GitHub after they've been tested in the lab. If you break this rule because it's 10pm and you have an urge to just fix one thing, you will eventually ship a bug at the worst possible moment.

A corollary: treat the Mac Mini like a server, not a computer. You don't tinker on servers. You SSH in, read logs, restart services, and get out. The Mac Mini should have one job: running AdministrateMe reliably for your family. Don't browse the web on it. Don't install other apps. Don't let it become a "general purpose computer." The more it does, the more ways it can fail.

### Discipline 2: Save often. Describe honestly.

Every time you have a working change, commit it. "Working" is a low bar — the thing you just changed does the thing you wanted and didn't obviously break anything else you tested. Don't wait until you've built a perfect feature to commit. Commit small and often, like saving every paragraph when you're writing an important email. A day's work should be 5–20 commits, not one. If you lose 20 minutes of work, fine. If you lose an afternoon's work, that hurts.

The commit message is a note-to-future-you. Be honest. "added grocery anomaly detection for items over $10" is a good commit message. "fixed stuff" is a bad one. In six months, when you're trying to figure out when something broke, commit messages are your only guide. If they all say "fixed stuff" you will hate your past self.

### Discipline 3: One thing at a time.

When you start a change, finish that change before starting the next one. Don't be halfway through adding grocery anomaly detection and then also start changing how the morning digest looks. Commit the anomaly work, get it working or throw it away, THEN start the digest change.

This sounds obvious. You will violate it constantly. You'll be in the middle of something and have another idea and start tugging at a different thread. That's when things get tangled and you can't untangle them. When you catch yourself about to start a second thing while a first thing is unfinished: stop. Either (a) finish or throw away the first thing, or (b) explicitly park the first thing ("git, remember this for me, I'll come back") before starting the second.

Claude Code can help here. If you ask it to do two things at once, it may do both (which is fine if small) or do them poorly (which is worse). A good habit: "Before you make any changes, tell me what you're about to change and ask if that's what I want."

### Summary

- Lab for experiments, family for running. Never reverse them.
- Save often with honest notes.
- One change at a time.

Those three disciplines are almost the whole game. The rest of this guide is mechanics for living by them.

---

## Chapter 3: The two phases and the two machines

There are two phases to getting AdministrateMe running, and they involve different setups.

### Phase A — Claude Code builds the code on GitHub

For the initial build, Claude Code runs in Anthropic's sandbox, works against a GitHub repository, and produces all the code. You don't need any Mac setup for Phase A. You don't need a Mac Mini yet. You don't need a lab. You just need:

- A GitHub account (you have one).
- The repo at https://github.com/junglecrunch1212/administrate-me-now (you have it).
- A browser to open Claude Code in.

You paste prompts into Claude Code, one at a time, in order. Claude Code works against GitHub — reading the spec, writing code to a branch, committing, pushing. You review the PRs in GitHub when you have time. This takes ~2-4 weeks of Saturday sessions, one or two prompts per slot.

Phase A ends when prompt 18 passes. At that point the repo contains all the application code, the bootstrap wizard, the CLI, the persona packs, and the mirrored external documentation. The system exists as code. It doesn't run anywhere yet.

### Phase B — you bootstrap the Mac Mini

Now you need hardware. You sit down at the Mac Mini, install some prerequisites, clone the repo, run `./bootstrap/install.sh`, and walk through the wizard. At the end, the family instance is live — receiving iMessages, reading calendars, watching transactions.

This is when the two-machine setup from the original design matters:

**Machine A: the Mac Mini (family instance).**
- M4 Mac Mini, base model is fine ($599 as of early 2026).
- Lives on a shelf near your router. Plugged into power and Ethernet, lid shut on its own display if you even have one (usually you don't; remote in via Screen Sharing).
- Runs the family copy of AdministrateMe. Connects to your real accounts.
- Updated on purpose, on a schedule, by you. Never tinkered with live.

**Machine B: your MacBook (lab — OPTIONAL, used for testing future changes).**
- Whatever Mac you use normally. MacBook Air or Pro, any chip from M1 onward.
- Runs a second copy of AdministrateMe in a separate folder, with fake data.
- Useful for testing new features or debugging production issues before they touch the family instance.
- **You don't need this to start.** It's a future convenience, not a prerequisite. Many operators run only a Mac Mini for months and add a lab only when they start modifying the system.

**GitHub: the bridge.**
- Both machines (and Claude Code's sandbox) talk to GitHub.
- Phase A: Claude Code's sandbox ↔ GitHub.
- Phase B and ongoing: GitHub ↔ Mac Mini (and optionally ↔ MacBook lab).
- GitHub is also your safety net: if the Mac Mini dies, the code is still there.

```
Phase A (build):                    Phase B+ (running, optionally extending):

 ┌─────────────────────┐             ┌─────────────────┐
 │  Claude Code        │             │     GitHub      │
 │  (Anthropic sandbox)│             │  (the internet) │
 └──────────┬──────────┘             └─────┬───────────┘
            │                              │
            │ push to branches             │       ┌────(optional)────┐
            ▼                              ▼       ▼                  │
 ┌──────────────────────┐           ┌──────────┐  ┌──────────┐        │
 │       GitHub         │           │ Mac Mini │  │ MacBook  │        │
 │   administrate-me-now│           │ (family) │  │  (lab)   │────────┘
 └──────────────────────┘           │          │  │          │   (push changes
                                    │real data │  │fake data │    back to GitHub
                                    │must work │  │break ok  │    → Mac Mini pulls)
                                    └──────────┘  └──────────┘
```

### Why not one machine for the family instance?

The Mac Mini has to be always-on and uncluttered; your MacBook goes to sleep, travels, closes its lid. For the family instance you need a machine that just sits there and runs.

Technically you could use a MacBook as the family instance if you never let it sleep, but then the laptop isn't really a laptop anymore. A Mac Mini is cheap enough ($600 range) that I'd strongly nudge you to just buy one. It pays for itself the first time you avoid a "why did the morning digest not send today?" incident.

### The first-time cost (Phase B)

- Mac Mini M4 base: ~$600
- HDMI cable + USB-C adapter for initial setup: ~$20. After setup you remote in via Screen Sharing; never need a monitor again.
- Keyboard + mouse for setup: use something you have.
- Ethernet cable: ~$10.

Call it $650 total. One-time. You don't need anything for Phase A (the build).

### If you already have a second Mac for a lab

If you have a desktop iMac or old Mac you're willing to dedicate as a lab, that works — skip the Mac Mini purchase only if that second machine can be always-on and dedicated to AdministrateMe. For most people the simpler setup is: Mac Mini = family instance (required for Phase B), your existing MacBook = optional lab added later when you want to test changes.

---

## Chapter 4: Phase A — running the build

Phase A is when Claude Code generates the codebase. You don't need a Mac Mini, a lab, or any Mac-side setup. You need:

1. A browser.
2. The repo at https://github.com/junglecrunch1212/administrate-me-now (already exists; all the spec files and the prompt sequence are in it).
3. Time, about 2-4 weeks of Saturday sessions.

### Step 1: Open Claude Code

Claude Code in this context is Anthropic's agentic coding tool running in a sandboxed environment. You can access it from claude.ai — look for the Claude Code option, or the equivalent in whatever current surface Anthropic offers. It will spin up a sandbox, authenticate to GitHub, and work against your repo.

### Step 2: Paste the universal preamble, then prompt 00

Open `prompts/PROMPT_SEQUENCE.md` in the repo. Near the bottom, you'll see "The universal preamble (paste before every prompt)" — a block starting with "**Phase + repository + documentation + sandbox discipline.**" Copy that whole block.

Then open `prompts/00-preflight.md`. That's the first prompt.

In Claude Code, paste the preamble, press Enter (or add a blank line), paste prompt 00. Claude Code will:
- Verify it has repo access.
- Check its sandbox has the tools it needs (Python 3.11+, Node 22+, git, gh).
- Produce `docs/preflight-report.md`.
- Commit to a branch and push.

It'll stop and tell you it's done. Review the report in GitHub. If it looks good, close that Claude Code session.

### Step 3: Fresh session, prompt 00.5

Open a new Claude Code session (fresh context is important — each prompt should get its own session). Paste the universal preamble + `prompts/00.5-mirror-docs.md`. This one populates `docs/reference/` with mirrored external documentation fetched from public GitHub repos (OpenClaw, Plaid, BlueBubbles, Google APIs, etc.). Takes 45-75 minutes of Claude Code work.

Review when it's done. It'll produce a `_status.md` and `_gaps.md` in `docs/reference/`. Gaps are expected — specifically, Apple EventKit documentation isn't on GitHub anywhere, so that's flagged for manual clipping. Handle that when convenient (~15 minutes of you copying 4 pages from developer.apple.com into markdown files).

### Step 4: Continue through the sequence

One prompt per session, in order. The full list is in `prompts/PROMPT_SEQUENCE.md` — 00 → 00.5 → 01 → 01b → 02 → 03 → 04 → 05 → 06 → 07 → 07.5 (checkpoint) → 08 → 09a → 09b → 10a → 10b → 10c → 10d (checkpoint) → 11 → 12 → 13a → 13b → 14a → 14b → 14c → 14d → 14e (checkpoint) → 15 → 15.5 (checkpoint) → 16 → 17 → 18 → 19.

Each prompt produces a PR branch. Review PRs in GitHub. Merge them when they look right (or ask Claude Code to fix issues in a follow-up session).

When prompt 18 passes, Phase A is done. The repo is build-complete.

### Step 5: Phase A integration test passes → you're ready for Phase B

At prompt 18, Claude Code runs a full integration test using lab mode (in its sandbox, with mocked external services). If that passes, you have a build-complete repo. Don't run it against your real accounts yet — that's Phase B.

---

## Chapter 4.5: Phase B — bootstrapping the Mac Mini

Now you need the Mac Mini. Plan to dedicate a Saturday morning; it takes 2-4 hours end to end.

### Step 1: Set up the Mac Mini physically

If you haven't already: unbox, plug in power + Ethernet + HDMI temporarily. Sign in with your Apple ID (a new one you create for the assistant — not your personal one; the assistant uses this Apple ID to receive iMessages). System Preferences → Users & Groups → enable auto-login (the machine should come back up unattended after power outages). Sharing → enable Screen Sharing so you can remote in from your MacBook later.

### Step 2: Install prerequisites on the Mac Mini

Open Terminal. Paste each block, press Enter, wait.

```
# Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Core tooling
brew install node@22 python@3.11 git gh 1password-cli

# OpenClaw gateway
brew install openclaw    # or follow docs/reference/openclaw/install/installer.md if not yet on Homebrew

# Tailscale
brew install --cask tailscale
# Open Tailscale, sign in with your Tailscale account

# BlueBubbles Server
# Download from https://bluebubbles.app/downloads/ and install the macOS server app
# Configure per the BlueBubbles docs
```

### Step 3: Clone the repo

```
mkdir -p ~/Documents
cd ~/Documents
gh auth login   # authenticate to GitHub; use a personal access token
gh repo clone junglecrunch1212/administrate-me-now adminme
cd adminme
```

### Step 4: Install AdministrateMe's dependencies

```
cd ~/Documents/adminme
poetry install
cd console && npm install && cd ..
```

### Step 5: Run bootstrap

This is the big one. The bootstrap wizard walks you through nine sections over ~2 hours. Have your credentials handy:

- Apple ID password (the new one for the assistant)
- Google Workspace admin (if you have one) or Google account
- Plaid credentials (you'll create these during bootstrap — signup is in the wizard)
- 1Password CLI must be authenticated (`op signin`)

Run it:

```
cd ~/Documents/adminme
./bootstrap/install.sh
```

Answer the wizard's questions. Each of the nine sections is resumable — if something goes wrong in section 5, you can fix it and restart the wizard; it'll pick up at section 5 rather than starting over.

### Step 6: Run the Phase B smoke test

Immediately after bootstrap completes:

```
./scripts/phase-b-smoke-test.sh
```

This runs 12 checks across the whole stack (OpenClaw reachable, BlueBubbles receiving, services healthy, event log responsive, projections populated, observation mode active). All 12 should pass. If any fail, see `docs/PHASE_B_SMOKE_TEST.md` for interpretation + recovery.

### Step 7: Observation mode period (7 days)

Observation mode is ON by default after bootstrap. For 7 days, the system receives all incoming messages, events, and transactions, and composes outbound responses + suggestions — but does NOT send anything externally. Suppressed outbound is logged.

Every day for 7 days, check `adminme observation log`. Read what AdministrateMe WOULD have sent. If the drafts look sensible, let the clock run. If they're weird (wrong tone, wrong timing, wrong addressees), that's information about what to fix before going live.

When the 7 days are up and the suppressed log looks consistently correct:

```
adminme observation off
```

The family instance is now live. Real iMessages will send. Real digests will compose and dispatch.

### Step 8 (optional): Set up the lab on your MacBook

Months from now, when you want to modify or extend AdministrateMe, you'll want a lab. That's when you set up a MacBook as Machine B from Chapter 3 — clone the repo, run bootstrap in `--lab-mode` with fake credentials, experiment there, push changes up, pull to Mac Mini when vetted. But this is future work, not a day-one requirement.

---

## Chapter 5: How to actually work with Claude Code

Claude Code is the coder. You are the product manager. This chapter is about how to talk to it so you get what you want.

### Principle 1: Be specific about WHAT. Trust Claude Code about HOW.

Bad: "make the morning digest better."
Good: "make the morning digest shorter when there are more than 5 items — cut the 'context' line for any item past the first 3."

Bad: "fix the grocery thing."
Good: "the grocery list isn't syncing with Apple Reminders — when I add an item in Reminders, it's not showing up in the console for 10+ minutes. I think the sync daemon might be stuck. Investigate and fix."

You don't need to suggest how to fix things. You often don't know. What you do know is what you want to happen, or what you're seeing that's wrong. Tell Claude Code that, and let it figure out where in the code to change what.

### Principle 2: Ask for a plan before changes.

When you want a non-trivial change, start with:

> Before you make any changes, explain your plan. What files will you change, what will you add, and is there anything I should know before you start?

Claude Code will tell you. You read the plan. If it sounds reasonable, say "go ahead." If it sounds like a lot, say "start with just the first thing on your plan and stop for my review."

This is the single most-useful habit. It catches 80% of the "wait, that's not what I meant" misunderstandings before any code is written.

### Principle 3: Test before you commit.

Every change needs to be tested in the lab before you commit it. "Test" doesn't mean run some fancy automated test suite. "Test" means: restart the relevant service, click through the thing that changed, see if it does what you wanted and didn't break what it did before.

Here's a habit: after Claude Code makes a change, don't immediately commit. Instead, tell it:

> Walk me through how to test this change. What do I click, what do I type, what should I see?

Follow the steps. If the thing works: commit. If it doesn't: tell Claude Code what happened and ask it to fix it.

### Principle 4: Commit small; commit often.

When you're iterating on something, it's tempting to wait until it's "done" to commit. Resist that. Commit every time you get to a working state, even if that state is partial. This way if you go down a wrong path for an hour and want to back up, you're only giving up an hour, not a day.

A reasonable day of work might have commits like:

- "add grocery anomaly detection (threshold: $10)"
- "make the threshold configurable, default $10"
- "add a test case for the threshold"
- "show the anomaly count in the finance dashboard"
- "fix: anomaly count was counting completed items too"

Five commits, each one a reachable checkpoint. If commit 4 broke something, you can back up to 3.

### Principle 5: When Claude Code goes off the rails, stop.

Sometimes Claude Code will do something weird — change a bunch of files you didn't ask about, introduce a scary-looking restructuring, or get stuck in a loop trying to fix something that isn't broken. When this happens:

1. Say "stop."
2. Tell it: "revert everything you changed in this session. I want to start over."
3. Think about what you asked for. Was it ambiguous? Did you ask for too many things at once?
4. Try again with a narrower, more specific request.

The worst thing you can do when Claude Code is spiraling is let it keep going while hoping. It's not going to recover on its own. Stop, revert, start over.

### Principle 6: Screenshot the weirdness.

When something is visually wrong — the reward toast looks weird, a table is misaligned, the scoreboard's stars are the wrong color — take a screenshot and paste it into Claude Code. "Here's what it looks like. I want it to look like [describe]." Claude Code is very good at visual feedback. Saying "it looks wrong" is much harder to act on than "it looks like this."

### Principle 7: Keep sessions focused.

A "session" with Claude Code is a conversation. Start a new session when you're changing topics. Don't add the digest change into the same conversation where you were debugging Plaid this morning. Fresh conversation, fresh focus, fewer errors.

---

## Chapter 6: The vocabulary you actually need

Now that you know what you're doing, here are the words. Each one maps to one of the four activities from chapter 1.

### Repository (repo)

The folder with all the code in it. GitHub has the canonical copy on the internet (at https://github.com/junglecrunch1212/administrate-me-now). Your Mac Mini has a local copy at `~/Documents/adminme/`. If you set up a lab MacBook, it has another copy at `~/Documents/adminme-lab/`. When people say "the repo," they usually mean the GitHub copy.

### Commit

A save with a note. You did a thing, you like the thing, you commit. The commit lives in the history of the repo forever. You can always go back to a commit.

A commit has three parts: the files that changed, a message (what did this commit do?), and a timestamp. Good commits are small and do one thing. Bad commits are huge and tangled.

### Branch

A parallel version of the repo. You can have multiple branches at once — one called `main` that's "the official version," one called `grocery-anomalies` that's "the version where I'm trying grocery anomalies." When the grocery work is done and good, you merge the `grocery-anomalies` branch back into `main`.

Why branches? So you can work on two things at once without tangling them. Or so you can abandon an experiment without losing your good stuff. Or so your Mac Mini only ever runs code from `main`, while your MacBook experiments on feature branches.

**For a solo vibe coder, a simple branch strategy:**

- `main` is always the "good" version — the version the Mac Mini runs.
- When you start a new experiment, create a branch off `main` (GitHub Desktop: Branch → New Branch). Name it something descriptive: `add-grocery-anomalies`, `try-new-digest-style`, `fix-plaid-sync`.
- Work on the branch. Commit often.
- When it works and you want it to become official, merge the branch into `main` (you'll do this via a pull request, explained next).
- Delete the branch. It's done its job.

### Pull request (PR)

A proposal to merge one branch into another, with a window for you to look at everything that changed one more time before it's official.

In a team, PRs are where coworkers review each other's work. For you, working alone, a PR is "me asking me one more time: do I really want these changes to become official?" It's useful because GitHub's PR interface shows you exactly what changed since `main`, all in one place. Much easier to spot "wait, what did that also touch?" in a PR than in a pile of individual commits.

The rhythm:

1. Work on a branch, commit often.
2. When you think you're done, open a pull request from your branch into `main`.
3. Look at the PR on GitHub's website. Read the list of changed files. Does it match what you were trying to do? Any surprise changes?
4. If yes, merge the PR. Your branch is now part of `main`.
5. If no, go back and fix the surprises (new commits on the same branch update the PR automatically).

### Merge

Taking one branch's changes and applying them to another. When you "merge the PR into main," you're taking all the commits from your branch and stitching them into `main`'s history. After the merge, `main` contains everything the branch had.

### Fork

A copy of someone else's repo, under your account, that you can change freely. If AdministrateMe's code lives at `github.com/administrateme/platform`, you might fork it to `github.com/yourname/platform` so you can make changes without affecting the original. Forks matter if you're building on top of someone else's project. For a standalone household deployment, you might not need to think about forks at all — your `main` is your `main`.

### Sync (pull / push)

"Push" means: take my local commits and send them up to GitHub. "Pull" means: take GitHub's commits and bring them down to my local copy. GitHub Desktop does both for you with buttons — you'll see "Push origin" or "Pull origin" near the top.

You push when you've made local commits and want them backed up / shared.
You pull when someone (including past-you on another machine) pushed commits you don't have yet.

### Main (or master)

The default branch. The "official" version. The one your Mac Mini runs from.

### Revert

Undo a commit. GitHub Desktop: right-click a commit → Revert. This makes a new commit that undoes the old one. You don't lose history; you just add a "take it back" commit.

### Reset (DANGEROUS)

Throw away commits as if they never happened. This can lose work if used wrong. Don't use reset casually. If you want to undo something, use revert. Leave reset for cases where Claude Code tells you specifically that reset is the right move.

### `.gitignore`

A file that lists things git should NEVER commit. Secrets, passwords, local-only configuration, fake-data folders, database files. You don't edit this often — Claude Code sets it up — but you should know it exists. If a secret accidentally ends up in a commit and pushes to GitHub, it's there forever, even if you delete it later. The `.gitignore` prevents that by telling git to skip those files.

### Stash

A way to temporarily set aside work-in-progress without committing it. "I need to go fix this other thing, but I'm not done with what I'm doing — let me stash this and come back." GitHub Desktop supports it. You'll use it occasionally, not often.

### `origin`

The name git uses for "the GitHub copy of this repo." When you see "push to origin," read it as "push to GitHub."

### `HEAD`

Git's bookmark for "the commit I'm currently looking at." You'll see this term in error messages. Mostly you can ignore it — it's git's internal bookkeeping.

### Summary

You now know more git vocabulary than most people who use it daily. You don't need more. If you ever hit a word not in this list, ask Claude Code: "what does [word] mean in the context of what I'm doing?" It will tell you.

---

## Chapter 7: The daily and weekly rhythm (post-Phase-B)

Once Phase B is complete and your Mac Mini is running the family instance, here's the operating rhythm.

### Daily (when you want to)

There's no requirement to change anything daily. If the system is working and you're happy, don't touch it. "If it ain't broke" is an underrated principle here.

When you DO want to make a change, you have two options for how Claude Code works on it:

**Option A — Claude Code in Anthropic's sandbox (simpler, no MacBook lab needed).** Just like during Phase A: open Claude Code in your browser, tell it what you want, it works against the GitHub repo, produces a PR branch. You review the PR, merge into `main` on GitHub. Then you deploy (see "Weekly deploy" below). This works well for most changes; it's what you're already comfortable with.

**Option B — Claude Code on a local MacBook lab (if you set one up — Chapter 3 Machine B).** Open Claude Code on your MacBook pointed at `~/Documents/adminme-lab/`. Work directly against the lab instance; see changes live; iterate faster on visual tweaks or interactive debugging. Push branches to GitHub when changes are ready. Merge into `main`. Deploy to Mac Mini.

You can use either or both. Option A is simpler for surgical changes. Option B is faster for "I want to click around and see what changes feel like." Most operators start with A and add B only when they miss having a live lab.

Either way, the workflow ends the same: **changes land on `main` in GitHub, then get deployed to the Mac Mini.**

### Weekly (deploy day)

Pick a day and time that you're usually around and can notice problems. Saturday mornings work well for most households. Call this your "deploy slot."

On deploy day:

1. Remote into the Mac Mini via Screen Sharing from your MacBook.
2. Open Terminal on the Mac Mini.
3. `cd ~/Documents/adminme` (where the family instance lives).
4. Pull the latest `main` from GitHub: `git pull origin main`.
5. Run migrations + restart services. Exactly how depends on what changed that week. `adminme deploy --check` tells you what's needed; `adminme deploy --apply` runs it. If in doubt, ask Claude Code at the time.
6. Run the smoke test: `./scripts/phase-b-smoke-test.sh`. All checks should pass. Two minutes.
7. Click around the console briefly. Trigger a morning digest preview. Make sure the scoreboard renders.
8. If anything looks wrong, **roll back**: `git reset --hard <previous commit hash>`, restart services. Write down the rollback procedure specifically so you don't have to think when it's stressful. Keep it printed near the Mac Mini.
9. If everything looks right: deploy done. Close Screen Sharing. Have breakfast.

### Monthly (maintenance)

Once a month:

- **Back up the event log and instance directory.** The event log is the irreplaceable file — everything else can be rebuilt from it. Copy `~/.adminme/events/event_log.db` (and the sidecar files) to at least two places: Backblaze B2, external drive, iCloud Drive, whatever. AdministrateMe ships `adminme backup` for this; use it. Calendar reminder.
- **Review observation log.** Even after the initial 7-day observation period, observation mode can be toggled on briefly for sanity checking. `adminme observation log` shows recent suppressions.
- **Update dependencies.** Ask Claude Code: "check for any security updates in our dependencies and apply any non-breaking ones." Test (sandbox or lab). Deploy Saturday.
- **Clean up old branches.** On GitHub's branches page, delete branches you merged a while back.

### Yearly-ish (big moves)

When you want to do something big — migrate to a new macOS, swap Mac Mini hardware, add a new adapter that requires new credentials — don't try to do it in one session. Plan over a weekend. Back up first. Have a clear rollback path. Tell the family "heads up, Poopsy may be quiet Saturday while I work on something."

---

## Chapter 8: When things go wrong

They will. Here's how to not panic.

### Three categories of wrong

**Category 1: The lab is broken.** You made a change, the lab won't start, or does something weird. This is fine. Resources to draw on: Claude Code (describe what happened, paste error messages), rolling back (`git reset --hard <previous good commit>`), and full reset (delete lab data folder, re-bootstrap). You have infinite lives here.

**Category 2: The deploy went wrong.** You deployed to the Mac Mini and something's broken. The family is affected. This is stressful but manageable. The rollback (`git reset --hard <previous commit>` and restart services) is your first move. Then investigate in the lab, not on the Mac Mini. Once you know what's wrong, fix it in the lab, redeploy.

**Category 3: The Mac Mini has been broken for a while, and I didn't notice.** This is the nightmare scenario, and it's the one the "deploy slot + smoke test" habit exists to prevent. If you catch it within a week, roll back to last week's version, investigate in the lab. If you catch it later, you might have corrupted data; ask Claude Code to help assess, and restore from your most recent backup if needed.

### Pattern recognition

**This is fine, keep going:**
- Claude Code warned you that the change affects files X and Y, and you can see it only changed files X and Y.
- After a change, the lab started normally and did the new thing.
- A test did exactly what you expected.

**This is weird, I should revert:**
- Claude Code changed more files than you expected.
- After a change, the lab starts but things look different in ways you didn't ask for.
- A test did what you expected, but something else stopped working.
- Claude Code is iterating a lot on the same problem and making it worse.

**This is on fire, call for help:**
- The event log is corrupted or won't open.
- The Mac Mini won't start AdministrateMe at all, and rollback doesn't help.
- Secrets (passwords, API keys) got committed to GitHub.
- The family is unable to use the system for more than a few hours.

For "call for help" situations, "help" means Claude Code itself in a fresh session, armed with the error messages. Paste the full error, describe what you did leading up to it, and say "the family is affected, I need to restore service. Walk me through what to do." Claude Code will give you a plan.

### The "I broke the family instance" playbook

This is the playbook you hope you never need but that removes 80% of the fear. Print it. Tape it near the Mac Mini.

1. **Don't panic.** You have a rollback. This is fine.
2. **Note the time.** Write down what you were doing when it broke.
3. **SSH into the Mac Mini** (Screen Sharing or `ssh` from Terminal).
4. **Navigate to the repo:** `cd ~/Documents/adminme` (or your family-instance path).
5. **See recent commits:** `git log --oneline -20`. You see a list of the last 20 commits. The topmost is the one currently deployed.
6. **Find the last known-good commit.** Usually the one right before your last deploy. You'll recognize it by the commit message.
7. **Roll back:** `git reset --hard <that commit's hash>` (first 7 characters of the hash is fine).
8. **Restart services:** `adminme services restart` (or whatever command Claude Code gave you for this).
9. **Verify:** open the console from your MacBook, click around, make sure things look right.
10. **Tell Claude Code** what happened. It'll investigate in the lab and propose a fix.

That's 10 steps. Reading it should take 90 seconds. Doing it should take 5 minutes. The family is back online.

### The leaked-secret playbook

If you accidentally commit a password, API key, or any credential to GitHub, treat it as already public. The moment it hits GitHub's servers, assume bots have seen it.

1. **Rotate the credential immediately.** Go to the service (Plaid, Anthropic, Google, whatever) and generate a new one. The old one is dead.
2. **Remove it from the repo.** Ask Claude Code: "I accidentally committed [description of secret]. Help me remove it from git history and make sure it won't happen again."
3. **Update `.gitignore`** to prevent the file from being committed in the future.
4. **Update the config file** on both Mac Mini and MacBook with the new credential.

Don't try to delete the repo and re-upload. That doesn't work. Rotate the credential.

---

## Chapter 9: Improving AdministrateMe without breaking it

Now the fun part. You have a working household system. What do you change?

### Categories of improvements, easiest to hardest

**Cosmetic (very safe):** colors, text, the reward messages Poopsy uses, the morning digest wording, the scoreboard emoji for each chore. If you change "Hot take: that was great." to "Nice one." — you cannot hurt anything. These are string changes in persona and profile packs. Do as many as you want.

**Behavioral tuning (safe):** reward tier probabilities (make jackpot rarer?), paralysis detection hours, morning digest delivery time, how many tasks the carousel shows per day, what hour "morning" starts for Laura. These are config changes. Low risk, reversible.

**New surfaces (moderate):** add a new settings pane, add a new column to the Finance dashboard, make a new view that shows "upcoming bills for the next 30 days." You're adding code but not changing existing code. Medium risk.

**New features (harder):** add grocery anomaly detection. Add a new skill that reads a PDF and extracts data. Add a new integration with some service. You're writing substantive new code. Test carefully.

**Platform changes (expert-level):** changing how the event log works, changing a projection's schema, changing the rate limiter. Don't do these without a specific reason and a lot of caution. Migrations are hard. Ask Claude Code to walk you through both the change AND the migration plan before you commit.

**Never do:** edit the event log directly. Delete projection databases without a backup. Change governance rules (action gates, hard refuses) without thinking hard about what you're allowing. Bypass observation mode when you're testing a new outbound integration.

### The "weekend project" sweet spot

The best kinds of improvements are ones where:

- The scope is small enough to finish in a weekend.
- The risk if it goes wrong is low (can roll back easily, not affecting critical flows).
- You'll actually use the thing after you build it.

Examples:
- "Add a 'hosting balance' widget to the CRM that shows top 5 friends with the biggest hosting imbalance."
- "When the scoreboard gets a jackpot, play a little celebration sound on the kitchen iPad."
- "Add a slash command `/recap` that summarizes the last 7 days."
- "Customize the morning digest to always mention today's weather."

Examples NOT to do on a weekend:
- "Replace SQLite with Postgres."
- "Add support for a new phone network adapter I just thought of."
- "Rewrite the pipeline scheduler."

### How to ship a feature end-to-end

Here's the full dance for a feature you're adding. Use it as a template.

1. **Frame the feature in one sentence.** "When I complete a grocery list item, the Finance dashboard should decrement the 'grocery spend remaining' estimate by the item's cost if the item has a price attached."
2. **Ask Claude Code for a plan.** "Here's what I want. Before making changes, tell me which files you'd change, what new concepts you'd introduce, and any tests you'd add."
3. **Read the plan. Push back if needed.** "I don't want a new database table for this. Can you store the price as a field on the existing list_item events?"
4. **Approve, then go.** Claude Code changes files.
5. **Test in the lab.** Add a grocery item with a price, mark it complete, check the dashboard.
6. **Commit. Small messages. Honest.** "add: optional price field on list_item events"; "add: decrement finance estimate on list_item.completed"; "ui: show the decrement in finance dashboard tooltip."
7. **Open a PR on GitHub.** Read the diff. Does it match your mental model?
8. **Merge the PR.** Your branch is absorbed into `main`.
9. **Deploy to Mac Mini on Saturday.** Smoke test. Done.

Total time: 1–4 hours depending on scope.

### Knowing when a change is "done"

You know a change is done when:

- It does the thing you set out to do.
- It doesn't break any other thing you noticed.
- Committing it feels satisfying, not anxious.

If committing it feels anxious ("am I sure this is right?"), that's a signal. Don't commit. Test more. Or back up and redo.

### The "dumb idea" discipline

You will have ideas that sound great at 10pm and are actually terrible. "What if Poopsy wrote a weekly family newsletter and emailed it to everyone?" (What could go wrong? Everything.) "What if the scoreboard ordered chores by Charlie's past completion rate?" (Optimizing for compliance = optimizing for the wrong thing in childhood.)

For any feature idea, ask:
- Is this adding something the family needs, or am I entertaining myself?
- If I build this and don't like it in a week, can I remove it cleanly?
- Does this add complexity the family will feel, or only complexity I will feel?

If the answers are "entertaining myself / not cleanly / complexity for the family" — shelf it. If the answers are "family needs / can remove cleanly / only my complexity" — build it.

---

## Chapter 10: The long game

AdministrateMe is a ten-year project if it's a day. This chapter is about making sure it's still serving you and your family in year 10, not abandoned in year 2 because you "broke it and quit."

### The abandonment threshold

Software projects like this get abandoned when the "cost of keeping it running" crosses the "benefit of it running." For you, benefit is high — it's your household's CoS, it knows things no replacement would. So you need to keep the cost of running it LOW.

Cost goes up when:
- You can't remember how to do routine ops ("what was that command again?")
- You're afraid to deploy because last time broke something.
- It needs weird attention at inconvenient times.
- You're having to learn new technical skills just to keep it alive.

Cost stays low when:
- You have a playbook for anything recurring.
- Rollbacks are easy and tested.
- You defer updates to planned windows instead of doing them reactively.
- The system's normal state is "working silently in the background."

### The "I forgot how this works" problem

Six months from now, you'll want to make a change and you'll have forgotten how to set up the lab, what the commit rhythm was, where the Mac Mini's hostname is stored. This is completely normal. Solution: write it down while you do it. Make a `NOTES.md` file in your repo. Add things like:

```
## deploy to mac mini
1. ssh adminme-hub.tail-abc123.ts.net
2. cd ~/Documents/adminme
3. git pull origin main
4. python -m adminme migrate
5. adminme services restart
6. tail logs: `tail -f ~/.adminme/logs/adminme.jsonl`

## lab reset
rm -rf ~/adminme-lab-data
ADMINME_INSTANCE_DIR=~/adminme-lab-data ./bootstrap/install.sh --lab-mode

## rollback
git reset --hard <previous-commit-hash>
adminme services restart

## last backup: 2026-04-01, backblaze b2, bucket 'stice-adminme'
```

Update these notes every time you do an operation you're going to forget. Future-you will thank present-you.

### The "what broke?" diary

Keep a diary of anything that broke, how you fixed it, and what caused it. Even one-liners are fine:

```
2026-03-14: scoreboard stopped updating. cause: xlsx_sync daemon crashed silently.
            fix: restarted it. longer fix: Claude Code added a watchdog.

2026-04-02: morning digest didn't send Monday. cause: OpenClaw was in observation
            mode after a crash. fix: turned observation off. also set up a
            startup script to ensure observation is off after restart.
```

Over time, patterns emerge. "Oh, the xlsx_sync daemon is flaky — Claude Code, help me add persistent supervision to it." You'll see problems early instead of late.

### When to ask for help vs. muddle through

Ask Claude Code for help when:
- Anything is broken and the family can't use it (even minor).
- You want to add a feature and don't know where to start.
- You see a log message you don't understand.
- A deploy didn't go the way you expected.

Muddle through on your own when:
- You're learning. Some struggle builds skill.
- The thing is working; you're just curious how. Claude Code can explain but you don't need it to.
- It's late and you're tired. Go to sleep. Ask tomorrow.

### Scaling up — when the family grows

Baby arriving, kids getting older, new vendor relationships: AdministrateMe handles all of this via config changes, not code changes. A new member is `adminme member add ...` via the CLI. A new Plaid institution is `adminme plaid link ...`. You'll want to do these in the lab first to see what happens, then on the Mac Mini.

### Scaling up — when Claude Code gets better

New Claude models will ship. You'll want to keep up. Use the lab to try new models on non-critical tasks first. Claude 5, Claude 6 — they'll be better, but "better at what" will matter. Don't replace a working setup just because a new model dropped; upgrade when there's a reason.

### The worst-case scenario plan

What if, in year 3, you fall off AdministrateMe for a while? Six months without commits, no deploys, not checking in? The worst case is:

- Mac Mini keeps running. The family keeps using it. Nothing changes.
- Some small things start to feel stale (calendar cruft, old commitments the system thinks are still owed), but nothing breaks.
- Eventually Plaid tokens expire, requiring a re-link. That's the first thing that'll feel like neglect.

Nothing about "not maintaining" causes data loss. The event log is append-only; nothing gets corrupted by inattention. The worst effect of not maintaining is the system gradually feeling less fresh, less tuned to current life. When you come back, you restart where you left off. No catastrophe.

That's by design. AdministrateMe is built to be a ten-year household system, which means it's built to survive occasional operator-absence. Don't fear taking a break if you need one.

### The long-term promise

If you hold the three disciplines from chapter 2 — experiment in lab, save often with honest notes, one thing at a time — you can run this system for a decade. Your kids will grow up with Poopsy. Laura's legal practice will evolve alongside the comms routing rules. Your finance projections will accumulate history nobody could replicate. The baby becoming a principal when she turns 10 is a five-line config change.

You didn't learn to code. You learned to direct a coder, review what they produce, save the good stuff, and go back when needed. That's enough for a lifetime.

---

## Appendix A: Rollback flowchart

When something breaks, follow this:

```
      SOMETHING IS BROKEN
              │
              ▼
   ┌──────────────────────┐
   │ Is it the lab?       │
   └──────────────────────┘
       │             │
      YES           NO (family instance)
       │             │
       ▼             ▼
 ┌─────────┐   ┌─────────────────────┐
 │ Who     │   │ Can you live with   │
 │ cares.  │   │ it for 5 minutes?   │
 │ Reset   │   └─────────────────────┘
 │ or      │       │             │
 │ revert. │      YES           NO
 └─────────┘       │             │
                   ▼             ▼
           ┌──────────┐   ┌────────────────┐
           │ Investi- │   │ ROLL BACK NOW. │
           │ gate in  │   │ git reset hard │
           │ lab.     │   │ restart svc.   │
           │ Fix in   │   │ Investigate    │
           │ lab.     │   │ after family   │
           │ Deploy   │   │ is served.     │
           │ fix.     │   └────────────────┘
           └──────────┘
```

## Appendix B: The printable cheat sheet

Print this. Tape it by the Mac Mini.

```
  EVERYDAY COMMANDS
  ─────────────────
  Start lab:       cd ~/Documents/adminme-lab && ./start-lab.sh
  Reset lab data:  rm -rf ~/adminme-lab-data && re-bootstrap
  See recent commits:  git log --oneline -20
  Deploy to Mac Mini (Saturday):
    ssh adminme-hub
    cd ~/Documents/adminme
    git pull origin main
    adminme migrate
    adminme services restart
    (smoke test)

  ROLLBACK
  ─────────
  git reset --hard <commit_hash>
  adminme services restart

  BACKUP (monthly)
  ──────────────
  adminme instance backup

  EMERGENCY (family can't use system)
  ─────────────────────────────────
  1. Don't panic
  2. ssh Mac Mini
  3. git log --oneline -20
  4. git reset --hard <last known good>
  5. adminme services restart
  6. Verify; tell Claude Code in lab
```

## Appendix C: The vocabulary quick-reference

- **Repo** = folder of code
- **Commit** = save + note
- **Branch** = parallel version
- **Main** = official version (Mac Mini runs this)
- **PR** = "do I want this branch merged?"
- **Merge** = absorb a branch into another
- **Pull** = get changes from GitHub
- **Push** = send changes to GitHub
- **Revert** = undo a commit (safe)
- **Reset** = delete commits (dangerous; use carefully)
- **Origin** = GitHub's copy
- **Fork** = your copy of someone else's repo
- **Lab instance** = MacBook, fake data, experiments
- **Family instance** = Mac Mini, real data, no experiments

## Appendix D: When this guide runs out

When you hit a situation this guide doesn't cover:

1. Ask Claude Code. "[This guide] doesn't cover [this situation]. What should I do?"
2. Claude Code will explain. Write it into your NOTES.md file.
3. If it feels universally useful, tell me and I'll update this guide.

---

*This guide is not comprehensive. It doesn't need to be. It's a starting kit for operating AdministrateMe as a non-coder with a family to serve. Everything here, used, beats everything a software engineering textbook would teach you that you'd never use.*

*Go set up the lab. Then read chapter 5 again. Then make your first commit.*
