"""CrashGuard - live crash alert dashboard for the ESP32 vehicle crash detection project.

Flow: ESP32 -> ThingSpeak (cloud) -> this Streamlit app -> Twilio SMS -> emergency contacts
"""

import copy
import html
import json
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="CrashGuard", page_icon="🚨", layout="wide")

IST = ZoneInfo("Asia/Kolkata")
SETTINGS_FILE = "settings.json"
POLL_EVERY = "10s"        # how often the live monitor checks for new data
ONLINE_WINDOW_S = 90      # ESP32 counts as online if it reported within this many seconds
ALERT_WINDOW_S = 300      # a crash stays "active" on screen for this many seconds
PHONE_RE = re.compile(r"^\+\d{8,15}$")

DEFAULT_TEMPLATE = (
    "CRASH ALERT: {vehicle} may have been in an accident at {time} on {date}. "
    "Location: {maps_link} Impact: {g_force} g, tilt {tilt} deg. "
    "Please check on them or call 112."
)
PRESETS = {
    "Short": "CRASH ALERT {time}: {vehicle}. Location: {maps_link}",
    "Detailed": DEFAULT_TEMPLATE + " Coordinates: {lat}, {lon}.",
    "Formal": (
        "Automatic emergency notification: a possible collision involving {vehicle} was "
        "detected on {date} at {time}. Last known location: {maps_link} "
        "If you cannot reach the occupants, please contact emergency services (112)."
    ),
}
VARIABLES = {
    "{vehicle}": "Vehicle name you set below",
    "{time}": "Time of the crash (IST)",
    "{date}": "Date of the crash",
    "{maps_link}": "Google Maps link to the crash location",
    "{lat}": "Latitude",
    "{lon}": "Longitude",
    "{g_force}": "Peak impact in g",
    "{tilt}": "Tilt angle in degrees",
}
DEFAULTS = {
    "contacts": [],
    "template": DEFAULT_TEMPLATE,
    "vehicle": "RC car prototype",
    "armed": True,
    "dry_run": True,
    "last_alert_id": None,
    "log": [],
}

# --------------------------------------------------------------------------- styling

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;700&family=DM+Sans:wght@400;500;700&display=swap');
html, body, .stApp, button, input, textarea, select { font-family: 'DM Sans', sans-serif; }
h1, h2, h3, .cg-display { font-family: 'Chakra Petch', sans-serif !important; letter-spacing: .02em; }
.stApp {
  background:
    radial-gradient(900px 500px at 8% -8%, rgba(25,244,255,.10), transparent 60%),
    radial-gradient(800px 500px at 100% 0%, rgba(255,46,136,.10), transparent 60%),
    #070A12;
}
.cg-title { font-family: 'Chakra Petch', sans-serif; font-weight: 700; font-size: 2.4rem; line-height: 1.1;
  color: #19F4FF; text-shadow: 0 0 18px rgba(25,244,255,.55); margin: 0 0 .2rem 0; }
.cg-tag { color: #9fb0d8; margin: 0 0 1.2rem 0; }

.cg-hero { display: flex; gap: 1.4rem; align-items: center; padding: 1.4rem 1.6rem; border-radius: 14px;
  background: #0E1424; border: 1px solid var(--c); box-shadow: 0 0 24px -6px var(--c), inset 0 0 30px -18px var(--c);
  margin: .4rem 0 1.2rem 0; }
.cg-hero.ok { --c: #19F4FF; } .cg-hero.alert { --c: #FF2E88; } .cg-hero.idle { --c: #8A5BFF; }
.cg-ring { width: 56px; height: 56px; border-radius: 50%; border: 3px solid var(--c); display: grid; place-items: center;
  flex: none; box-shadow: 0 0 16px var(--c); }
.cg-dot { width: 20px; height: 20px; border-radius: 50%; background: var(--c); }
.cg-hero.alert .cg-ring { animation: cg-pulse 1.1s ease-in-out infinite; }
@keyframes cg-pulse { 0%,100% { transform: scale(1); box-shadow: 0 0 10px var(--c); }
  50% { transform: scale(1.12); box-shadow: 0 0 30px var(--c); } }
.cg-headline { font-family: 'Chakra Petch', sans-serif; font-size: 1.6rem; font-weight: 700; color: var(--c); }
.cg-detail { color: #c8d3f0; }

.cg-flow { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin: .6rem 0 1.4rem 0; }
.cg-step { background: #0E1424; border-radius: 12px; padding: 1rem 1.1rem; border: 1px solid #1b2547;
  border-top: 3px solid var(--c); box-shadow: 0 -8px 22px -14px var(--c); }
.cg-step b { font-family: 'Chakra Petch', sans-serif; font-size: 1.15rem; color: var(--c); }
.cg-step .by { color: #8ea0cf; font-size: .85rem; margin: .1rem 0 .5rem 0; }
.cg-step p { margin: 0; color: #dbe3fb; font-size: .95rem; }

.cg-path { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: .4rem 0 1rem 0; }
.cg-chip { padding: .45rem .8rem; border-radius: 999px; border: 1px solid var(--c); color: var(--c); background: #0E1424;
  font-size: .92rem; }
.cg-arrow { color: #5b6a99; }

.cg-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }
.cg-card { background: #0E1424; border: 1px solid #1b2547; border-left: 3px solid var(--c); border-radius: 12px;
  padding: 1.1rem 1.2rem; }
.cg-card h4 { font-family: 'Chakra Petch', sans-serif; color: var(--c); margin: 0 0 .35rem 0; font-size: 1.1rem; }
.cg-card p { margin: 0; color: #dbe3fb; font-size: .95rem; }

.cg-sms { background: #0b1020; border: 1px dashed #19F4FF; border-radius: 14px; padding: 1rem 1.2rem; color: #e6ecff;
  box-shadow: 0 0 18px -8px #19F4FF; white-space: pre-wrap; word-break: break-word; }

@media (prefers-reduced-motion: reduce) { .cg-hero.alert .cg-ring { animation: none; } }
</style>
"""


def render_html(markup: str) -> None:
    """Render raw HTML (strips indentation so markdown doesn't treat it as a code block)."""
    st.markdown("".join(line.strip() for line in markup.splitlines()), unsafe_allow_html=True)


# --------------------------------------------------------------------------- settings storage

def load_settings() -> dict:
    data = copy.deepcopy(DEFAULTS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                data.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return data


def save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def secret(name: str):
    try:
        return st.secrets[name]
    except Exception:
        return None


# --------------------------------------------------------------------------- data from the ESP32 (via ThingSpeak)

def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_feed(feed: dict):
    """ThingSpeak fields: 1 = crash flag (0/1), 2 = latitude, 3 = longitude, 4 = g-force, 5 = tilt."""
    try:
        when = (
            datetime.strptime(feed["created_at"], "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)
            .astimezone(IST)
        )
        return {
            "id": int(feed["entry_id"]),
            "time": when,
            "crash": num(feed.get("field1")) == 1,
            "lat": num(feed.get("field2")),
            "lon": num(feed.get("field3")),
            "g": num(feed.get("field4")),
            "tilt": num(feed.get("field5")),
        }
    except (KeyError, TypeError, ValueError):
        return None


def get_events(n: int = 10):
    channel = secret("THINGSPEAK_CHANNEL_ID")
    if not channel:
        return [], "not_configured"
    params = {"results": n}
    read_key = secret("THINGSPEAK_READ_KEY")
    if read_key:
        params["api_key"] = read_key
    try:
        r = requests.get(
            f"https://api.thingspeak.com/channels/{channel}/feeds.json", params=params, timeout=10
        )
        r.raise_for_status()
        feeds = r.json().get("feeds", [])
    except (requests.RequestException, ValueError) as exc:
        return [], f"Could not reach ThingSpeak: {exc}"
    events = [e for e in (parse_feed(f) for f in feeds) if e]
    return sorted(events, key=lambda e: e["id"]), None


# --------------------------------------------------------------------------- SMS

class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def fmt(value, digits=1):
    return "n/a" if value is None else f"{value:.{digits}f}"


def has_fix(ev: dict) -> bool:
    return ev.get("lat") not in (None, 0.0) and ev.get("lon") not in (None, 0.0)


def build_message(template: str, ev: dict, vehicle: str) -> str:
    maps = (
        f"https://maps.google.com/?q={ev['lat']:.6f},{ev['lon']:.6f}"
        if has_fix(ev)
        else "(location unavailable - no GPS fix)"
    )
    values = _SafeDict(
        vehicle=vehicle,
        time=ev["time"].strftime("%I:%M:%S %p") + " IST",
        date=ev["time"].strftime("%d %b %Y"),
        maps_link=maps,
        lat=fmt(ev.get("lat"), 6),
        lon=fmt(ev.get("lon"), 6),
        g_force=fmt(ev.get("g")),
        tilt=fmt(ev.get("tilt"), 0),
    )
    try:
        return template.format_map(values)
    except (ValueError, IndexError):
        return template


def sms_segments(text: str) -> int:
    unicode_text = any(ord(c) > 127 for c in text)
    single, multi = (70, 67) if unicode_text else (160, 153)
    return 1 if len(text) <= single else -(-len(text) // multi)


def send_sms(to: str, body: str):
    sid, token, sender = (
        secret("TWILIO_ACCOUNT_SID"),
        secret("TWILIO_AUTH_TOKEN"),
        secret("TWILIO_FROM_NUMBER"),
    )
    if not (sid and token and sender):
        return False, "Twilio secrets are missing"
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={"To": to, "From": sender, "Body": body},
            timeout=15,
        )
        if r.status_code in (200, 201):
            return True, "Sent"
        try:
            return False, r.json().get("message", f"HTTP {r.status_code}")
        except ValueError:
            return False, f"HTTP {r.status_code}"
    except requests.RequestException as exc:
        return False, f"Network error: {exc}"


def dispatch(ev: dict, S: dict):
    """Send the alert to every contact (or just log it in dry-run mode). Returns (sent, failed, info)."""
    if not S["contacts"]:
        return 0, 0, "No emergency contacts yet. Add some in the Contacts tab."
    body = build_message(S["template"], ev, S["vehicle"])
    stamp = datetime.now(IST).strftime("%d %b %H:%M:%S")
    sent = failed = 0
    for c in S["contacts"]:
        if S["dry_run"]:
            ok, status = True, "Dry run (not sent)"
        else:
            ok, status = send_sms(c["phone"], body)
        sent += ok
        failed += not ok
        S["log"].insert(0, {"Logged at": stamp, "Contact": c["name"], "Status": status, "Message": body})
    S["log"] = S["log"][:40]
    save_settings(S)
    return sent, failed, body


# --------------------------------------------------------------------------- UI pieces

def hero(state: str, headline: str, detail: str) -> None:
    render_html(
        f"""
        <div class="cg-hero {state}">
          <div class="cg-ring"><div class="cg-dot"></div></div>
          <div>
            <div class="cg-headline">{html.escape(headline)}</div>
            <div class="cg-detail">{html.escape(detail)}</div>
          </div>
        </div>
        """
    )


def ago(when: datetime) -> str:
    secs = int((datetime.now(IST) - when).total_seconds())
    if secs < 60:
        return f"{max(secs, 0)} s ago"
    if secs < 3600:
        return f"{secs // 60} min ago"
    return when.strftime("%d %b, %I:%M %p")


@st.fragment(run_every=POLL_EVERY)
def live_monitor() -> None:
    S = load_settings()
    events, err = get_events()
    now = datetime.now(IST)

    # On the very first check, ignore old history so we never text about past crashes.
    if S["last_alert_id"] is None and err is None:
        S["last_alert_id"] = max((e["id"] for e in events), default=0)
        save_settings(S)

    fired = None
    paused = False
    new_crashes = [e for e in events if e["crash"] and e["id"] > (S["last_alert_id"] or 0)]
    if new_crashes and err is None:
        ev = max(new_crashes, key=lambda e: e["id"])
        S["last_alert_id"] = ev["id"]
        if S["armed"]:
            fired = dispatch(ev, S)
        else:
            paused = True
            save_settings(S)

    if err and err != "not_configured":
        st.error(err)
    if err == "not_configured":
        st.info(
            "ThingSpeak isn't connected yet, so there's no live ESP32 data. "
            "You can still try everything with the simulator at the bottom of this tab."
        )

    if fired:
        sent, failed, info = fired
        if sent == 0 and failed == 0:
            st.warning(info)
        elif failed:
            st.error(f"Crash detected. {sent} alert(s) sent, {failed} failed. Check the log below.")
        elif S["dry_run"]:
            st.success(f"Crash detected. Dry run: {sent} alert(s) logged, no real SMS sent.")
        else:
            st.success(f"Crash detected. SMS sent to {sent} contact(s).")
    if paused:
        st.warning("Crash detected, but automatic alerts are switched off in the sidebar.")

    latest = events[-1] if events else None
    crashes = [e for e in events if e["crash"]]
    last_crash = crashes[-1] if crashes else None
    online = latest is not None and (now - latest["time"]).total_seconds() < ONLINE_WINDOW_S

    if last_crash and (now - last_crash["time"]).total_seconds() < ALERT_WINDOW_S:
        hero("alert", "Crash detected", f"Reported {ago(last_crash['time'])} at {last_crash['time'].strftime('%I:%M:%S %p')} IST.")
    elif online:
        hero("ok", "Armed and online", f"The ESP32 last reported {ago(latest['time'])}. No crash detected.")
    else:
        hero("idle", "Waiting for the ESP32", "No recent readings. Check the car's power and Wi-Fi hotspot.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Last reading", ago(latest["time"]) if latest else "none yet")
    c2.metric("Impact (g)", fmt(latest["g"]) if latest else "n/a")
    c3.metric("Tilt (deg)", fmt(latest["tilt"], 0) if latest else "n/a")
    c4.metric("Crashes logged", len(crashes))

    left, right = st.columns(2)
    with left:
        st.markdown("##### Last crash location")
        if last_crash and has_fix(last_crash):
            st.map(pd.DataFrame({"lat": [last_crash["lat"]], "lon": [last_crash["lon"]]}), size=40, color="#FF2E88")
            st.caption(f"{last_crash['lat']:.5f}, {last_crash['lon']:.5f} at {last_crash['time'].strftime('%d %b %I:%M %p')} IST")
        else:
            st.caption("No crash with a GPS fix yet.")
    with right:
        st.markdown("##### Impact over the last readings")
        rows = [{"time": e["time"].strftime("%H:%M:%S"), "g": e["g"]} for e in events if e["g"] is not None]
        if rows:
            st.line_chart(pd.DataFrame(rows).set_index("time"), color="#19F4FF")
        else:
            st.caption("Waiting for g-force readings.")

    st.markdown("##### Recent alerts")
    if S["log"]:
        st.dataframe(pd.DataFrame(S["log"]), hide_index=True)
    else:
        st.caption("Nothing sent yet. Alerts will show up here.")

    with st.expander("Try it without the ESP32"):
        st.caption("Sends a fake crash through the same pipeline. Respects the dry-run switch in the sidebar.")
        s1, s2 = st.columns(2)
        lat = s1.number_input("Latitude", value=12.9692, format="%.6f", key="sim_lat")
        lon = s2.number_input("Longitude", value=79.1559, format="%.6f", key="sim_lon")
        if st.button("Simulate a crash", key="sim_btn"):
            fake = {"id": None, "time": datetime.now(IST), "lat": lat, "lon": lon, "g": 4.2, "tilt": 78.0}
            sent, failed, info = dispatch(fake, load_settings())
            if sent == 0 and failed == 0:
                st.warning(info)
            elif failed:
                st.error(f"{sent} sent, {failed} failed. See the log above after the next refresh.")
            else:
                st.success(f"Done: {sent} alert(s) {'logged (dry run)' if S['dry_run'] else 'sent'}.")


def how_it_works() -> None:
    st.subheader("From impact to SMS in five steps")
    steps = [
        ("Sense", "MPU-6050", "Reads acceleration and rotation many times a second.", "#19F4FF"),
        ("Decide", "ESP32", "Combines impact, tilt and spin rate, so potholes and hard braking don't set it off.", "#8A5BFF"),
        ("Locate", "NEO-6M GPS", "Adds the position and the time of the crash.", "#B6FF3B"),
        ("Report", "Wi-Fi to ThingSpeak", "Pushes the crash record to the cloud within seconds.", "#FFB020"),
        ("Alert", "This website", "Texts the time and a map link to every emergency contact.", "#FF2E88"),
    ]
    cards = "".join(
        f'<div class="cg-step" style="--c:{c}"><b>{t}</b><div class="by">{by}</div><p>{d}</p></div>'
        for t, by, d, c in steps
    )
    render_html(f'<div class="cg-flow">{cards}</div>')

    st.subheader("What changes after a crash")
    render_html(
        """
        <div class="cg-path" style="--c:#8A5BFF">
          <span class="cg-chip">Without CrashGuard</span>
          <span class="cg-chip">Crash</span><span class="cg-arrow">&rarr;</span>
          <span class="cg-chip">Occupants may be unable to call</span><span class="cg-arrow">&rarr;</span>
          <span class="cg-chip">Wait for a passer-by</span><span class="cg-arrow">&rarr;</span>
          <span class="cg-chip">Someone phones for help</span>
        </div>
        <div class="cg-path" style="--c:#19F4FF">
          <span class="cg-chip">With CrashGuard</span>
          <span class="cg-chip">Crash</span><span class="cg-arrow">&rarr;</span>
          <span class="cg-chip">Sensor detects it automatically</span><span class="cg-arrow">&rarr;</span>
          <span class="cg-chip">Contacts get time and location</span><span class="cg-arrow">&rarr;</span>
          <span class="cg-chip">Help is called sooner</span>
        </div>
        """
    )


def benefits() -> None:
    st.subheader("Why it's worth building")
    items = [
        ("Hands-free", "It works even when the occupants are hurt or unconscious and can't make a call.", "#FF2E88"),
        ("Fewer false alarms", "Impact, tilt and spin rate must agree, so one bump on a rough road isn't treated as a crash.", "#19F4FF"),
        ("Exact place and time", "Every SMS carries a map link and a timestamp, so responders don't have to guess.", "#B6FF3B"),
        ("Low cost", "An ESP32, an MPU-6050 and a GPS module. Cheap parts that anyone can get.", "#FFB020"),
        ("Your words", "Contacts get the message you wrote, in the style you want.", "#8A5BFF"),
        ("Fits SDG 3, 9 and 11", "Targets road-injury response, accessible embedded tech and safer transport.", "#19F4FF"),
    ]
    cards = "".join(f'<div class="cg-card" style="--c:{c}"><h4>{t}</h4><p>{d}</p></div>' for t, d, c in items)
    render_html(f'<div class="cg-cards">{cards}</div>')


def _set_template(text: str) -> None:
    st.session_state["tpl"] = text


def sms_editor(S: dict) -> None:
    st.subheader("Write the alert your contacts will get")
    if "tpl" not in st.session_state:
        st.session_state["tpl"] = S["template"]

    left, right = st.columns([3, 2])
    with left:
        st.text_input("Vehicle name", value=S["vehicle"], key="veh")
        st.caption("Start from a preset, then edit freely.")
        cols = st.columns(len(PRESETS) + 1)
        for col, (name, text) in zip(cols, PRESETS.items()):
            col.button(name, key=f"pre_{name}", on_click=_set_template, args=(text,))
        cols[-1].button("Reset", key="pre_reset", on_click=_set_template, args=(DEFAULT_TEMPLATE,))
        st.text_area("Message", key="tpl", height=170)
        text = st.session_state["tpl"]
        if "{maps_link}" not in text:
            st.warning("Your message has no {maps_link}, so contacts won't get the crash location.")
        st.caption(f"{len(text)} characters, about {sms_segments(text)} SMS part(s) per contact.")
        if st.button("Save message", type="primary"):
            S["template"] = text
            S["vehicle"] = st.session_state["veh"].strip() or DEFAULTS["vehicle"]
            save_settings(S)
            st.success("Saved. New alerts will use this message.")
    with right:
        st.markdown("##### Preview")
        sample = {"time": datetime.now(IST), "lat": 12.9692, "lon": 79.1559, "g": 4.2, "tilt": 78.0}
        preview = build_message(text, sample, st.session_state["veh"] or DEFAULTS["vehicle"])
        render_html(f'<div class="cg-sms">{html.escape(preview)}</div>'.replace("\n", "<br>"))
        st.markdown("##### Variables you can use")
        for var, meaning in VARIABLES.items():
            st.markdown(f"`{var}` {meaning}")


def contacts_tab(S: dict) -> None:
    st.subheader("Emergency contacts")
    st.caption("Use the international format, like +919876543210. Twilio trial accounts can only text numbers you've verified there.")
    with st.form("add_contact", clear_on_submit=True):
        name = st.text_input("Name", placeholder="Mum")
        phone = st.text_input("Phone number", placeholder="+919876543210")
        if st.form_submit_button("Add contact"):
            phone = re.sub(r"[\s-]", "", phone)
            if not name.strip():
                st.error("Enter a name.")
            elif not PHONE_RE.match(phone):
                st.error("Phone must start with + and the country code, e.g. +919876543210.")
            else:
                S["contacts"].append({"name": name.strip(), "phone": phone})
                save_settings(S)
                st.rerun()

    if not S["contacts"]:
        st.info("No contacts yet. Add at least one so alerts have somewhere to go.")
    for i, c in enumerate(S["contacts"]):
        a, b, d = st.columns([3, 3, 1])
        a.write(c["name"])
        b.write(c["phone"])
        if d.button("Remove", key=f"del_{i}"):
            S["contacts"].pop(i)
            save_settings(S)
            st.rerun()


# --------------------------------------------------------------------------- page

st.markdown(CSS, unsafe_allow_html=True)
S = load_settings()


def _save_toggle(field: str, widget_key: str) -> None:
    current = load_settings()
    current[field] = st.session_state[widget_key]
    save_settings(current)


with st.sidebar:
    st.markdown("### Controls")
    st.toggle("Send alerts automatically", value=S["armed"], key="w_armed", on_change=_save_toggle, args=("armed", "w_armed"))
    st.toggle("Dry run (no real SMS)", value=S["dry_run"], key="w_dry", on_change=_save_toggle, args=("dry_run", "w_dry"))
    st.caption("Keep this page open while the car is running. Alerts are sent while the page is open.")
    st.markdown("### Connections")
    st.write(("🟢" if secret("THINGSPEAK_CHANNEL_ID") else "🔴") + " ThingSpeak (ESP32 data)")
    twilio_ok = all(secret(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"))
    st.write(("🟢" if twilio_ok else "🔴") + " Twilio (SMS)")

st.markdown('<div class="cg-title">CrashGuard</div>', unsafe_allow_html=True)
st.markdown('<p class="cg-tag">Automatic crash detection that texts your emergency contacts the time and place.</p>', unsafe_allow_html=True)

tab_live, tab_how, tab_benefits, tab_sms, tab_contacts = st.tabs(
    ["Live monitor", "How it works", "Benefits", "SMS message", "Contacts"]
)
with tab_live:
    live_monitor()
with tab_how:
    how_it_works()
with tab_benefits:
    benefits()
with tab_sms:
    sms_editor(S)
with tab_contacts:
    contacts_tab(S)
