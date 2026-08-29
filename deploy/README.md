# Running the recurrent search on a schedule

Three ways, cheapest first.

## 1. Foreground (demo, and what a judge sees)

    uv run python -m src.agent.cli watch-daemon --every 30

Prints a line whenever a watch fires or an escalation times out. Ctrl-C stops it.

## 2. cron (a laptop, a VM)

    uv run python -m src.agent.cli cron        # prints the line to paste
    crontab -e

One tick a minute is plenty. A tick does two things: expire escalations past
their deadline (this is what makes the 120 s fail-closed timeout real when
nobody is watching a terminal) and poll every watch that is due.

## 3. systemd (a box that must survive a reboot)

    sudo cp deploy/trytrust-watch.{service,timer} /etc/systemd/system/
    sudo systemctl enable --now trytrust-watch.timer
    systemctl list-timers trytrust-watch

## In GCP (decision #11)

The watcher is a Cloud Run job, triggered by Cloud Scheduler with an OIDC token:

    gcloud scheduler jobs create http trytrust-tick \
      --schedule="* * * * *" \
      --uri="https://api.trytrust.lat/jobs/tick" \
      --oidc-service-account-email="watcher@PROJECT.iam.gserviceaccount.com" \
      --location=southamerica-east1

Ticks are safe to run concurrently: a fired watch stops firing, captures are
idempotent on the intent id, and an already-resolved escalation is a no-op.
