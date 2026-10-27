# Campsite Watcher

Checks **Kirk Creek** and **Kirby Cove** (Recreation.gov) and **Steep Ravine** campsites and cabins (ReserveCalifornia) about every 10 minutes for the dates you choose. When a site opens up for every night of your trip, it sends an alert to your phone with a link straight to the booking page.

It never logs in, never stores your passwords or card, and never books anything. Availability on both sites is public, so no account is needed to check it. You tap the link and book it yourself (keep yourself logged in to both sites on your phone so this takes under a minute).

It runs for free on GitHub, so your computer doesn't need to be on.

---

## Setup (about 15 minutes)

### 1. Put the code on GitHub

1. Make a free account at [github.com](https://github.com) if you don't have one.
2. Click **+** (top right) → **New repository**. Name it `campsite-watcher`, choose **Public**, and click **Create repository**.
   *Public is what makes it free. Nothing private goes in the code: your phone number and keys are stored separately as encrypted "secrets" (step 3) that nobody else can see.*
3. On the new repo page click **uploading an existing file**, and drag in **everything** from the unzipped `campsite-watcher` folder, including the `.github` folder, then click **Commit changes**.
   *On a Mac, `.github` is hidden in Finder. Press **Cmd + Shift + .** in the folder to show it.*

### 2. Choose how alerts reach you

You can use either option, or both at once. **Start with Option A tonight** so you're covered right away, then add real texts later if you want them.

#### Option A: Phone push alert (free, works in 5 minutes)
Arrives on your lock screen with a sound, just like a text.

1. Install the free **ntfy** app (search "ntfy" in the App Store or Google Play; more at [ntfy.sh](https://ntfy.sh)).
2. In the app tap **+** and subscribe to a topic name that's hard to guess, like `simon-camping-7Kq29xT`.
   *Treat the name like a password: anyone who knows it can see your alerts.*
3. Use that same name for the `NTFY_TOPIC` secret in step 3.

#### Option B: Real text messages (about $3–4/month, plus a one-time ~$19)
US carriers now require anyone sending automated texts to register first, even a single person texting themselves. Approval usually takes a few days to a couple of weeks.

1. Sign up at [twilio.com](https://www.twilio.com) and upgrade to a paid account (registration isn't allowed on the free trial).
2. Buy a **local** phone number (about $1/month).
3. Start **A2P 10DLC registration** (search "A2P 10DLC" in the Twilio console) and register as a **Sole Proprietor** (for people without a business). They'll text a code to your cell to confirm it's you. Fees: $4 one-time brand fee, $15 one-time campaign review, $2/month. Use your own name and address for the "business" fields.
4. For the campaign, describe it as: *"Personal alerts sent only to my own phone when a campsite I'm watching becomes available. One recipient, the account owner."*
5. Once approved, note your **Account SID** and **Auth Token** (on the Twilio console home page) for step 3.

### 3. Add your secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**. Add the ones for the option(s) you chose:

| Secret name | Value | Needed for |
|---|---|---|
| `NTFY_TOPIC` | your topic name, e.g. `simon-camping-7Kq29xT` | Option A |
| `TWILIO_ACCOUNT_SID` | from Twilio console, starts with `AC` | Option B |
| `TWILIO_AUTH_TOKEN` | from Twilio console | Option B |
| `TWILIO_FROM_NUMBER` | the Twilio number you bought, like `+14155550123` | Option B |
| `MY_PHONE_NUMBER` | your cell, like `+14155559876` | Option B |

### 4. Set your dates

Open `config.yaml` in your repo, click the pencil icon, and edit the `trips` list:

```yaml
trips:
  - check_in: 2027-04-09   # arrival date
    nights: 2
  - check_in: 2027-05-14
    nights: 1
```

Click **Commit changes**. You can edit this any time from your phone's browser too; the next check uses the new dates. Past trips are skipped automatically. To stop watching a campground, set it to `false` under `campgrounds`.

### 5. Turn it on and test

1. Go to the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
2. Click **Send test alert** → **Run workflow**. Your phone should buzz within a minute.
3. Click **Check campsites** → **Run workflow** to run a real check now. Click the run to see what it found.

That's it. From now on it checks every 10 minutes on its own.

---

## How it behaves

- **One alert per opening.** You're alerted once when a site opens. If that site gets booked by someone else and then frees up again later, you'll be alerted again.
- **Whole trip only.** For a 2-night trip, you're only alerted if the *same* site is free both nights.
- **It tells you if it breaks.** If a booking site can't be reached for about half an hour, you get a heads-up message, and another when it's working again. You won't be left silently missing openings.
- **Timing.** GitHub sometimes runs scheduled checks a few minutes late, so think "every 10–15 minutes".
- **Stays on.** GitHub pauses scheduled jobs in public repos after 60 days with no activity. The included *Keep alive* job makes a tiny commit monthly so that never happens.

## Good to know about these campgrounds

- **Steep Ravine cabins** are mostly given out by a lottery about 7 months ahead. Unclaimed and canceled cabins go back on sale, and cancellations inside the 6-month window are released at **8 a.m. the next day**. That's when this alert is most likely to fire, so keep your phone handy around 8 a.m.
- **Steep Ravine** closes in October for maintenance.
- **Kirby Cove** is open roughly March through November, and limits each camper to 3 nights per season.
- **Kirk Creek** books up about 6 months out, but cancellations happen regularly.

Booking pages, if you want to check by hand:
[Kirk Creek](https://www.recreation.gov/camping/campgrounds/233116) ·
[Kirby Cove](https://www.recreation.gov/camping/campgrounds/232491) ·
[Steep Ravine campsites](https://www.reservecalifornia.com/park/682/590) ·
[Steep Ravine cabins](https://www.reservecalifornia.com/park/682/766)

## Changing things

- **Check more or less often:** in `.github/workflows/check-campsites.yml`, change `*/10` to e.g. `*/15` (every 15 minutes). Please don't go below every 5 minutes; it's unkind to the sites and can get the checker blocked.
- **Run it on your own computer instead:** install Python 3.10+, then in this folder run `pip install -r requirements.txt` and `python watcher.py`. Use `python watcher.py --dry-run` to see results without sending anything. Set the same secrets as environment variables.
