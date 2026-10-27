"""
Campsite Watcher
================
Checks Recreation.gov and ReserveCalifornia for open sites at Kirk Creek,
Kirby Cove and Steep Ravine on the dates in config.yaml, and texts you
when something opens up. It never logs in and never books anything:
you get a link and book it yourself.

Usage:
    python watcher.py               # normal check (what the schedule runs)
    python watcher.py --dry-run     # check and print, but send nothing
    python watcher.py --test-alert  # send a test message to your phone
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.yaml"
STATE_PATH = HERE / "state" / "state.json"
PACIFIC = ZoneInfo("America/Los_Angeles")

# How many failed checks in a row before you get a "checker is having trouble" text.
# The schedule runs about every 10 minutes, so 3 ≈ half an hour of failures.
FAILURE_ALERT_THRESHOLD = 3

CAMPGROUNDS = {
    "kirk_creek": {
        "name": "Kirk Creek",
        "provider": "recgov",
        "id": 233116,
    },
    "kirby_cove": {
        "name": "Kirby Cove",
        "provider": "recgov",
        "id": 232491,
    },
    "steep_ravine_campsites": {
        "name": "Steep Ravine campsites",
        "provider": "reservecal",
        "place": 682,
        "facility": 590,
    },
    "steep_ravine_cabins": {
        "name": "Steep Ravine cabins",
        "provider": "reservecal",
        "place": 682,
        "facility": 766,
    },
}

RECGOV_MONTH_URL = "https://www.recreation.gov/api/camps/availability/campground/{id}/month"
RECGOV_SITE_URL = "https://www.recreation.gov/camping/campsites/{site_id}"
RECGOV_CAMPGROUND_URL = "https://www.recreation.gov/camping/campgrounds/{id}"
RESERVECAL_GRID_URL = (
    "https://california-rdr.prod.cali.rd12.recreation-management.tylerapp.com/rdr/search/grid"
)
RESERVECAL_BOOKING_URL = "https://www.reservecalifornia.com/park/{place}/{facility}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


# --------------------------------------------------------------------------
# Data shapes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Trip:
    check_in: date
    nights: int

    @property
    def night_dates(self) -> list[date]:
        return [self.check_in + timedelta(days=i) for i in range(self.nights)]

    def label(self) -> str:
        n = "night" if self.nights == 1 else "nights"
        return f"{self.check_in.strftime('%a %b %-d')} ({self.nights} {n})"


@dataclass
class Site:
    """One bookable site and the nights it's free."""
    name: str
    url: str
    free_nights: set[date] = field(default_factory=set)


@dataclass(frozen=True)
class Opening:
    campground_key: str
    site_name: str
    url: str
    trip: Trip

    @property
    def key(self) -> str:
        return f"{self.campground_key}|{self.site_name}|{self.trip.check_in}|{self.trip.nights}"


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def load_config(path: Path = CONFIG_PATH) -> tuple[list[Trip], list[str]]:
    raw = yaml.safe_load(path.read_text()) or {}
    today = datetime.now(PACIFIC).date()

    trips: list[Trip] = []
    for i, t in enumerate(raw.get("trips") or [], start=1):
        try:
            check_in = t["check_in"]
            if isinstance(check_in, str):
                check_in = date.fromisoformat(check_in)
            nights = int(t.get("nights", 1))
        except (KeyError, TypeError, ValueError) as e:
            raise SystemExit(f"config.yaml: trip #{i} is not valid ({e}). "
                             "Use check_in: YYYY-MM-DD and nights: a number.")
        if nights < 1:
            raise SystemExit(f"config.yaml: trip #{i} needs at least 1 night.")
        if check_in < today:
            print(f"Skipping trip on {check_in}: it's in the past.")
            continue
        trips.append(Trip(check_in, nights))

    enabled_raw = raw.get("campgrounds") or {}
    unknown = set(enabled_raw) - set(CAMPGROUNDS)
    if unknown:
        raise SystemExit(f"config.yaml: unknown campground(s): {', '.join(sorted(unknown))}. "
                         f"Valid names: {', '.join(CAMPGROUNDS)}")
    enabled = [k for k in CAMPGROUNDS if enabled_raw.get(k, True)]
    return trips, enabled


# --------------------------------------------------------------------------
# Fetching availability
# --------------------------------------------------------------------------

def _request(method: str, url: str, **kwargs) -> requests.Response:
    """HTTP request with a couple of polite retries."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.request(method, url, headers=HEADERS, timeout=25, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_error = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{method} {url} failed: {last_error}")


def _months_covering(dates: list[date]) -> list[date]:
    return sorted({d.replace(day=1) for d in dates})


def fetch_recgov(campground_id: int, trips: list[Trip]) -> dict[str, Site]:
    all_nights = [d for t in trips for d in t.night_dates]
    sites: dict[str, Site] = {}
    for month in _months_covering(all_nights):
        resp = _request(
            "GET",
            RECGOV_MONTH_URL.format(id=campground_id),
            params={"start_date": month.strftime("%Y-%m-01T00:00:00.000Z")},
        )
        merge_recgov_month(sites, resp.json())
        time.sleep(1.5)
    return sites


def merge_recgov_month(sites: dict[str, Site], payload: dict) -> None:
    for site_id, info in (payload.get("campsites") or {}).items():
        type_of_use = (info.get("type_of_use") or "").lower()
        site_type = (info.get("campsite_type") or "").upper()
        name = info.get("site") or str(site_id)
        # Skip day-use / picnic areas (Kirby Cove has one).
        if type_of_use == "day" or "PICNIC" in site_type or "DAY" in name.upper():
            continue
        site = sites.setdefault(
            str(site_id),
            Site(name=f"site {name}", url=RECGOV_SITE_URL.format(site_id=site_id)),
        )
        for stamp, status in (info.get("availabilities") or {}).items():
            if status == "Available":
                site.free_nights.add(date.fromisoformat(stamp[:10]))


def fetch_reservecal(place: int, facility: int, trips: list[Trip]) -> dict[str, Site]:
    sites: dict[str, Site] = {}
    booking_url = RESERVECAL_BOOKING_URL.format(place=place, facility=facility)
    for trip in trips:
        body = {
            "FacilityId": facility,
            "StartDate": trip.check_in.strftime("%m-%d-%Y"),
            "EndDate": trip.night_dates[-1].strftime("%m-%d-%Y"),
            "IsADA": False,
            "MinVehicleLength": 0,
            "UnitCategoryId": 0,
            "WebOnly": True,
            "UnitTypesGroupIds": [],
            "SleepingUnitId": 0,
            "UnitSort": "orderby",
            "InSeasonOnly": False,
        }
        resp = _request("POST", RESERVECAL_GRID_URL, json=body)
        merge_reservecal(sites, resp.json(), booking_url)
        time.sleep(1.5)
    return sites


def merge_reservecal(sites: dict[str, Site], payload: dict, booking_url: str) -> None:
    facility = payload.get("Facility") or {}
    for unit_id, unit in (facility.get("Units") or {}).items():
        if unit.get("AllowWebBooking") is False or unit.get("IsWebViewable") is False:
            continue
        name = unit.get("Name") or unit.get("ShortName") or f"unit {unit_id}"
        site = sites.setdefault(str(unit_id), Site(name=name, url=booking_url))
        for slice_ in (unit.get("Slices") or {}).values():
            if slice_.get("IsFree") and not slice_.get("IsBlocked"):
                site.free_nights.add(date.fromisoformat(str(slice_["Date"])[:10]))


def fetch_campground(key: str, trips: list[Trip]) -> dict[str, Site]:
    cg = CAMPGROUNDS[key]
    if cg["provider"] == "recgov":
        return fetch_recgov(cg["id"], trips)
    return fetch_reservecal(cg["place"], cg["facility"], trips)


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def find_openings(campground_key: str, sites: dict[str, Site], trips: list[Trip]) -> list[Opening]:
    openings = []
    for trip in trips:
        needed = set(trip.night_dates)
        for site in sites.values():
            if needed <= site.free_nights:
                openings.append(Opening(campground_key, site.name, site.url, trip))
    return openings


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------

def campground_page(key: str) -> str:
    cg = CAMPGROUNDS[key]
    if cg["provider"] == "recgov":
        return RECGOV_CAMPGROUND_URL.format(id=cg["id"])
    return RESERVECAL_BOOKING_URL.format(place=cg["place"], facility=cg["facility"])


def format_alerts(openings: list[Opening]) -> list[tuple[str, list[Opening]]]:
    """One short text per campground + trip, so each message has one clear link.
    Returns (message, openings covered by that message) pairs."""
    groups: dict[tuple[str, Trip], list[Opening]] = {}
    for o in openings:
        groups.setdefault((o.campground_key, o.trip), []).append(o)

    messages: list[tuple[str, list[Opening]]] = []
    for (cg_key, trip), group in sorted(groups.items(), key=lambda g: (g[0][1].check_in, g[0][0])):
        name = CAMPGROUNDS[cg_key]["name"]
        names = sorted(o.site_name for o in group)
        if len(group) == 1:
            what = f"{names[0]} is open"
            link = group[0].url
        else:
            shown = ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
            what = f"{len(group)} spots open ({shown})"
            link = campground_page(cg_key)
        messages.append((f"Campsite alert! {name}: {what} for {trip.label()}. Book now: {link}", group))
    return messages


# --------------------------------------------------------------------------
# Sending alerts (Twilio SMS and/or ntfy push)
# --------------------------------------------------------------------------

def channels_configured() -> list[str]:
    chans = []
    if all(os.environ.get(v) for v in
           ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "MY_PHONE_NUMBER")):
        chans.append("sms")
    if os.environ.get("NTFY_TOPIC"):
        chans.append("ntfy")
    return chans


def send_sms(text: str) -> None:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    resp = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data={"From": os.environ["TWILIO_FROM_NUMBER"], "To": os.environ["MY_PHONE_NUMBER"], "Body": text},
        headers={"Authorization": f"Basic {auth}"},
        timeout=20,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Twilio error {resp.status_code}: {resp.text[:300]}")


def send_ntfy(text: str, click_url: str | None = None) -> None:
    topic = os.environ["NTFY_TOPIC"]
    headers = {"Title": "Campsite alert", "Priority": "high", "Tags": "camping"}
    if click_url:
        headers["Click"] = click_url
    resp = requests.post(f"https://ntfy.sh/{topic}", data=text.encode(), headers=headers, timeout=20)
    if resp.status_code >= 300:
        raise RuntimeError(f"ntfy error {resp.status_code}: {resp.text[:300]}")


def notify(text: str, dry_run: bool = False) -> bool:
    """Send on every configured channel. Returns True if at least one worked."""
    print(f"ALERT: {text}")
    if dry_run:
        return True
    chans = channels_configured()
    if not chans:
        print("  (No alert channel is set up, so nothing was sent. See README.)")
        return False
    link = next((w for w in text.split() if w.startswith("https://")), None)
    ok = False
    for chan in chans:
        try:
            if chan == "sms":
                send_sms(text)
            else:
                send_ntfy(text, link)
            ok = True
        except Exception as e:  # keep going so the other channel still gets through
            print(f"  Sending via {chan} failed: {e}")
    return ok


# --------------------------------------------------------------------------
# State (what we've already alerted about, and failure streaks)
# --------------------------------------------------------------------------

def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"alerted": [], "failures": {}}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


# --------------------------------------------------------------------------
# Main check
# --------------------------------------------------------------------------

def run_check(trips: list[Trip], enabled: list[str], state: dict, dry_run: bool = False,
              fetch=fetch_campground) -> dict:
    previously_alerted = set(state.get("alerted", []))
    failures: dict[str, int] = dict(state.get("failures", {}))
    still_open: set[str] = set()
    new_openings: list[Opening] = []

    for key in enabled:
        name = CAMPGROUNDS[key]["name"]
        try:
            sites = fetch(key, trips)
        except Exception as e:
            failures[key] = failures.get(key, 0) + 1
            print(f"{name}: check failed ({failures[key]} in a row): {e}")
            if failures[key] == FAILURE_ALERT_THRESHOLD:
                notify(f"Heads up: the campsite checker can't reach {name} right now "
                       f"(failed {FAILURE_ALERT_THRESHOLD} times in a row). "
                       "It will keep trying. Check the site by hand if it's urgent.", dry_run)
            # Keep earlier alerts for this campground so a blip doesn't re-send them.
            still_open |= {k for k in previously_alerted if k.startswith(f"{key}|")}
            continue

        if failures.get(key, 0) >= FAILURE_ALERT_THRESHOLD:
            notify(f"Good news: the campsite checker can reach {name} again.", dry_run)
        failures[key] = 0

        openings = find_openings(key, sites, trips)
        print(f"{name}: {len(sites)} sites checked, {len(openings)} match your dates.")
        for o in openings:
            still_open.add(o.key)
            if o.key not in previously_alerted:
                new_openings.append(o)

    sent_keys: set[str] = set()
    for message, covered in format_alerts(new_openings):
        if notify(message, dry_run):
            # Only mark as alerted if the message actually went out, so a failed send retries next run.
            sent_keys |= {o.key for o in covered}

    if not new_openings:
        print("No new openings.")

    # Remember openings we've told you about while they stay open.
    # Once a site is booked by someone else it drops off, so if it frees up again you'll hear again.
    alerted = (previously_alerted & still_open) | sent_keys
    return {"alerted": sorted(alerted), "failures": failures,
            "last_run": datetime.now(PACIFIC).isoformat(timespec="seconds")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="check and print, but don't send or save anything")
    parser.add_argument("--test-alert", action="store_true", help="send a test message and exit")
    args = parser.parse_args(argv)

    if args.test_alert:
        chans = channels_configured()
        print(f"Alert channels set up: {', '.join(chans) or 'none'}")
        ok = notify("Test from your campsite checker. If you can read this, alerts are working!")
        return 0 if ok else 1

    trips, enabled = load_config()
    if not trips:
        print("No upcoming trips in config.yaml, nothing to check.")
        return 0
    print("Watching: " + ", ".join(CAMPGROUNDS[k]["name"] for k in enabled))
    print("Trips: " + "; ".join(t.label() for t in trips))

    new_state = run_check(trips, enabled, load_state(), dry_run=args.dry_run)
    if not args.dry_run:
        save_state(new_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
