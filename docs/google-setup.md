# Google Cloud setup (Google Tasks, or the Google Calendar API)

Only needed for `todo.source: tasks` or `calendar.source: google`. If you use iCal links
for your calendar and Keep (or nothing) for to-dos, skip this page.

Do these steps on your computer, not the Pi. It takes about 20 minutes once.

## 1. Create the project and enable the APIs
1. Go to https://console.cloud.google.com and create a project (any name).
2. **APIs & Services → Library**: enable **Google Tasks API** and **Google Calendar API**.

## 2. Configure sign-in (Google Auth Platform)
1. **Branding**: app name (e.g. `pi_household_dashboard`) and your email as support email.
2. **Audience**: choose **External**, then under *Test users* add your own Gmail address.
3. **Data Access → Add or remove scopes**: add `.../auth/tasks.readonly` and
   `.../auth/calendar.readonly`. Save.
4. **Clients → Create client → Desktop app**. Download the JSON, rename it
   `credentials.json`, and put it in the `pi_household_dashboard` folder.

## 3. Publish the app (so sign-in doesn't expire weekly)
Apps left in **Testing** mode lose their sign-in every 7 days. To publish, Google requires a
home page and privacy policy link:

1. Make a free page with [GitHub Pages](https://pages.github.com): a public repo named
   `<your-username>.github.io` with an `index.html` that explains the purpose
   (e.g. "A personal e-ink home display that shows my calendar and tasks. Used only by its owner.")
   and a `privacy.html` (e.g. "This app reads calendar events and tasks to display them on a
   home screen. It does not store or share any data.").
2. In **Branding**, set the home page to `https://<your-username>.github.io`, the privacy link
   to `https://<your-username>.github.io/privacy.html`, and add `<your-username>.github.io`
   under *Authorized domains*. Don't upload a logo (that triggers a mandatory review).
3. In **Audience**, click **Publish app**. You don't need to submit for verification.

If Branding shows "issues found from the previous verification attempt," you can ignore it;
that review only affects showing your logo and name on the sign-in screen.

## 4. Create the token
```bash
pip install google-auth-oauthlib      # on Windows: py -m pip install google-auth-oauthlib
python setup_google.py                # on Windows: py setup_google.py
```
Sign in. Google warns "Google hasn't verified this app": click **Advanced → Go to pi_household_dashboard
(unsafe)**, then **Allow**. This is your own app, so that's expected.

A `token.json` appears in the folder. Copy it to the Pi along with everything else
(`~/pi_household_dashboard/token.json`). Keep it private.

## Using a calendar other than your main one (`calendar.source: google`)
In Google Calendar, open the calendar's ⋮ menu → **Settings and sharing** → *Integrate calendar*,
copy the **Calendar ID**, and list it under `calendar.calendar_ids` in `config.yaml`.
