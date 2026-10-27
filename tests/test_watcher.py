"""Offline tests: sample responses shaped like the real Recreation.gov and
ReserveCalifornia APIs, so the matching and alert logic can be checked
without hitting either site."""

from datetime import date

import pytest

import watcher as w

FRI = date(2027, 4, 9)
SAT = date(2027, 4, 10)
SUN = date(2027, 4, 11)


def recgov_payload():
    return {
        "campsites": {
            "4243": {  # free Fri+Sat
                "site": "003", "loop": "KIRBY COVE CAMPGROUND AREA",
                "campsite_type": "TENT ONLY NONELECTRIC", "type_of_use": "Overnight",
                "availabilities": {
                    "2027-04-09T00:00:00Z": "Available",
                    "2027-04-10T00:00:00Z": "Available",
                    "2027-04-11T00:00:00Z": "Reserved",
                },
            },
            "96745": {  # only Friday free
                "site": "005", "loop": "KIRBY COVE CAMPGROUND AREA",
                "campsite_type": "TENT ONLY NONELECTRIC", "type_of_use": "Overnight",
                "availabilities": {
                    "2027-04-09T00:00:00Z": "Available",
                    "2027-04-10T00:00:00Z": "Reserved",
                },
            },
            "99999": {  # day-use area must be ignored even when "Available"
                "site": "Day Use", "loop": "Day Use Area",
                "campsite_type": "PICNIC", "type_of_use": "Day",
                "availabilities": {
                    "2027-04-09T00:00:00Z": "Available",
                    "2027-04-10T00:00:00Z": "Available",
                },
            },
        }
    }


def reservecal_payload():
    return {
        "Message": "",
        "Facility": {
            "FacilityId": 766,
            "Units": {
                "11": {"UnitId": 11, "Name": "Cabin 3", "AllowWebBooking": True, "IsWebViewable": True,
                       "Slices": {
                           "2027-04-09T00:00:00": {"Date": "2027-04-09", "IsFree": True, "IsBlocked": False},
                           "2027-04-10T00:00:00": {"Date": "2027-04-10", "IsFree": True, "IsBlocked": False},
                       }},
                "12": {"UnitId": 12, "Name": "Cabin 7", "AllowWebBooking": True, "IsWebViewable": True,
                       "Slices": {
                           "2027-04-09T00:00:00": {"Date": "2027-04-09", "IsFree": True, "IsBlocked": True},
                       }},
                "13": {"UnitId": 13, "Name": "Cabin 9", "AllowWebBooking": False,
                       "Slices": {
                           "2027-04-09T00:00:00": {"Date": "2027-04-09", "IsFree": True, "IsBlocked": False},
                       }},
            },
        },
    }


def test_trip_nights():
    assert w.Trip(FRI, 2).night_dates == [FRI, SAT]


def test_recgov_parsing_skips_day_use_and_matches_all_nights():
    sites = {}
    w.merge_recgov_month(sites, recgov_payload())
    assert "99999" not in sites
    two_nights = w.find_openings("kirby_cove", sites, [w.Trip(FRI, 2)])
    assert [o.site_name for o in two_nights] == ["site 003"]
    assert two_nights[0].url == "https://www.recreation.gov/camping/campsites/4243"
    one_night = w.find_openings("kirby_cove", sites, [w.Trip(FRI, 1)])
    assert sorted(o.site_name for o in one_night) == ["site 003", "site 005"]


def test_reservecal_parsing_ignores_blocked_and_non_web_units():
    sites = {}
    w.merge_reservecal(sites, reservecal_payload(), "https://www.reservecalifornia.com/park/682/766")
    openings = w.find_openings("steep_ravine_cabins", sites, [w.Trip(FRI, 1)])
    assert [o.site_name for o in openings] == ["Cabin 3"]


def test_message_single_and_multiple():
    t = w.Trip(FRI, 2)
    one = [w.Opening("kirby_cove", "site 003", "https://x/4243", t)]
    msgs = w.format_alerts(one)
    assert msgs[0][0] == ("Campsite alert! Kirby Cove: site 003 is open for Fri Apr 9 (2 nights). "
                          "Book now: https://x/4243")
    many = [w.Opening("kirk_creek", f"site 0{i}", "u", t) for i in range(1, 6)]
    text = w.format_alerts(many)[0][0]
    assert "5 spots open (site 01, site 02, site 03 +2 more)" in text
    assert text.endswith("https://www.recreation.gov/camping/campgrounds/233116")
    for msg, _ in msgs + w.format_alerts(many):
        assert len(msg) <= 320, "keep texts to two SMS segments or fewer"


def make_fetch(results):
    def fetch(key, trips):
        r = results[key]
        if isinstance(r, Exception):
            raise r
        return r
    return fetch


@pytest.fixture
def sent(monkeypatch):
    log = []
    monkeypatch.setattr(w, "notify", lambda text, dry_run=False: log.append(text) or True)
    return log


def test_alerts_once_then_again_after_site_rebooks_and_reopens(sent):
    trips = [w.Trip(FRI, 1)]
    open_site = {"a": w.Site("site 003", "https://x", {FRI})}
    closed = {"a": w.Site("site 003", "https://x", set())}

    state = w.run_check(trips, ["kirby_cove"], {}, fetch=make_fetch({"kirby_cove": open_site}))
    assert len(sent) == 1
    state = w.run_check(trips, ["kirby_cove"], state, fetch=make_fetch({"kirby_cove": open_site}))
    assert len(sent) == 1, "no repeat text while the same site stays open"
    state = w.run_check(trips, ["kirby_cove"], state, fetch=make_fetch({"kirby_cove": closed}))
    state = w.run_check(trips, ["kirby_cove"], state, fetch=make_fetch({"kirby_cove": open_site}))
    assert len(sent) == 2, "texts again when it opens up a second time"


def test_failure_alert_after_threshold_and_recovery(sent):
    trips = [w.Trip(FRI, 1)]
    state = {}
    boom = make_fetch({"kirk_creek": RuntimeError("HTTP 403")})
    for _ in range(w.FAILURE_ALERT_THRESHOLD + 2):
        state = w.run_check(trips, ["kirk_creek"], state, fetch=boom)
    assert len(sent) == 1 and "can't reach Kirk Creek" in sent[0]
    w.run_check(trips, ["kirk_creek"], state, fetch=make_fetch({"kirk_creek": {}}))
    assert "can reach Kirk Creek again" in sent[-1]


def test_failed_send_is_retried_next_run(monkeypatch):
    attempts = []
    monkeypatch.setattr(w, "notify", lambda text, dry_run=False: attempts.append(text) and False)
    trips = [w.Trip(FRI, 1)]
    fetch = make_fetch({"kirby_cove": {"a": w.Site("site 003", "https://x", {FRI})}})
    state = w.run_check(trips, ["kirby_cove"], {}, fetch=fetch)
    assert state["alerted"] == []
    w.run_check(trips, ["kirby_cove"], state, fetch=fetch)
    assert len(attempts) == 2


def test_config_skips_past_trips_and_rejects_unknown_campground(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("trips:\n  - check_in: 2020-01-01\n  - check_in: 2099-06-01\n    nights: 2\n"
                   "campgrounds:\n  kirk_creek: false\n")
    trips, enabled = w.load_config(cfg)
    assert trips == [w.Trip(date(2099, 6, 1), 2)]
    assert "kirk_creek" not in enabled and "kirby_cove" in enabled
    cfg.write_text("trips: []\ncampgrounds:\n  yosemite: true\n")
    with pytest.raises(SystemExit):
        w.load_config(cfg)


def test_shipped_config_is_valid():
    trips, enabled = w.load_config()
    assert set(enabled) == set(w.CAMPGROUNDS)
