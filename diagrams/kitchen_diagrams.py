"""
Kitchen H2 safety-control system — review diagrams.

Renders 4 diagrams with graphviz:
  1. state_machine       — modes & transitions
  2. safety_dataflow     — the single-core control path (Sensors -> SensorState
                            -> KitchenCore.update() -> OutputRequest -> Outputs
                            -> HW). There is no veto layer: danger is a
                            transition evaluated first, not a rewrite applied
                            after.
  3. hardware_io_map     — physical wiring: sensors, analog outs, relays, LEDs
  4. mqtt_topic_map      — pub/sub contract with server.py / centralPLC / website

Output: PNG + SVG for each, written next to this script.

Requires the Graphviz `dot` binary. On this machine it's installed via winget
at C:\\Program Files\\Graphviz\\bin but not yet on PATH for existing shells —
so we add it defensively before importing graphviz.
"""
import os
import sys

# Make sure `dot.exe` is findable regardless of the calling shell's PATH.
_GV_BIN = r"C:\Program Files\Graphviz\bin"
if _GV_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _GV_BIN

import graphviz  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Shared palette — an instrument-panel system: warm paper ground, ink linework,
# and three signal colours borrowed from relay-rack wiring convention (blue =
# live/normal circuit, red = safety-critical, grey = advisory/bookkeeping).
# State fills are desaturated tints of INK so they read as panel sectors
# rather than candy-bright blocks — this keeps COL_DANGER the only saturated
# red on the page, which is what makes the emergency path legible at a glance.
# ---------------------------------------------------------------------------
INK          = "#0f1720"   # near-black ink — borders, body text
PAPER        = "#f7f5f0"   # warm paper background
COL_NORMAL   = "#2166ac"   # blue   — normal experiment flow
COL_DANGER   = "#c0392b"   # red    — emergency / safety-override paths
COL_NEUTRAL  = "#5b6470"   # grey   — bookkeeping / info edges
COL_SENSOR   = "#1a7a4c"   # green  — raw sensor sample stream
COL_WAIT_BG  = "#eef2f6"
COL_ARMED_BG = "#fdf3d9"
COL_LEAK_BG  = "#fbe4e1"
COL_HOLD_BG  = "#ece3f5"
COL_VENT_BG  = "#e1f0e6"
COL_FULLVENT_BG = "#f6c9c2"
FONT = "Segoe UI"
FONT_MONO = "Consolas"

def base_digraph(name, rankdir="TB", size=None):
    g = graphviz.Digraph(name, format="svg")
    # splines="ortho" routes every edge as straight horizontal/vertical
    # segments joined by 90-degree bends — no curves. The ortho router does
    # NOT lay out normal edge labels (it warns and floats them), so callers
    # must use oedge() below, which places text as an anchored taillabel.
    # forcelabels keeps those labels from being dropped when space is tight;
    # the generous nodesep/ranksep give them room to sit beside their edge.
    g.attr(rankdir=rankdir, fontname=FONT, bgcolor=PAPER, pad="0.45",
            nodesep="0.7", ranksep="1.1", splines="ortho", forcelabels="true")
    if size:
        g.attr(size=size, ratio="compress")
    g.attr("node", fontname=FONT, fontsize="11", fontcolor=INK)
    g.attr("edge", fontname=FONT, fontsize="9.5")
    return g


def _boxed_label(text, color=INK, border=None):
    """Wrap label text in a small, tight box on a paper-coloured chip.

    Sized to its own text (no fixed width) so it stays a small chip rather
    than a wide column, and CELLPADDING is minimal so it hugs the arrow it
    sits beside instead of ballooning outward. An optional hairline border
    in the edge colour turns the chip into a legible little tag instead of
    text that just happens to float near a line.
    """
    rows = "".join(
        f'<TR><TD ALIGN="LEFT"><FONT COLOR="{color}" POINT-SIZE="9">'
        f'{html_escape(line)}</FONT></TD></TR>'
        for line in text.split("\n")
    )
    border_attr = f' COLOR="{border}" BORDER="1"' if border else ' BORDER="0"'
    return (f'<<TABLE {border_attr} CELLBORDER="0" CELLSPACING="0" CELLPADDING="3" '
            f'BGCOLOR="{PAPER}">{rows}</TABLE>>')


def html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def oedge(g, tail, head, label=None, labelcolor=None, tag_border=False, **kw):
    """edge() wrapper for ortho graphs.

    Labels go on as xlabels (Graphviz won't lay out plain edge labels under
    ortho routing) wrapped in a small chip so the text stays legible without
    turning into a wide panel. xlabels are drawn as a separate late pass in
    Graphviz — there's no supported way to force them fully behind edge
    strokes — so the box is kept minimal instead, to limit how much it can
    cover.

    Label colour defaults to the edge colour so text and line always agree.
    tag_border=True adds a hairline box in that same colour, turning the
    label into a little tag — used for the emergency edges so each one
    visibly names its own source instead of reading as a stray caption.
    """
    if label:
        edge_col = kw.get("color", INK)
        kw["xlabel"] = _boxed_label(label, color=labelcolor or edge_col,
                                     border=edge_col if tag_border else None)
    g.edge(tail, head, **kw)


def render(g: graphviz.Digraph, filename_stem: str):
    svg_path = g.render(filename=filename_stem, directory=OUT_DIR, cleanup=True)
    # Also render PNG explicitly (separate call keeps two clean outputs)
    g.format = "png"
    g.attr(dpi="170")
    png_path = g.render(filename=filename_stem, directory=OUT_DIR, cleanup=True)
    print(f"wrote {svg_path}")
    print(f"wrote {png_path}")


# ===========================================================================
# 1. STATE MACHINE
# ===========================================================================
def build_state_machine():
    g = base_digraph("state_machine", rankdir="TB")
    g.attr(label="Kitchen H2 Control — State Machine", labelloc="t",
           fontsize="16", fontname=FONT + " Semibold")
    # The five red emergency edges all run as parallel verticals into
    # FULLYVENT, each now carrying its own source tag (see the danger loop
    # below) — nodesep is widened further so each tag box has clear space
    # beside its line instead of the line cutting through the text.
    g.attr(nodesep="1.15", ranksep="1.3")

    def node(name, label, fill, shape="box", peripheries="1"):
        g.node(name, label, shape=shape, style="filled,rounded", fillcolor=fill,
               color=INK, penwidth="1.3", peripheries=peripheries)

    node("WAITING",
         "WAITING\n"
         "(selector \u2264 2.5V: leak-test role — flowmeter sealed)\n"
         "(selector > 2.5V: equipment-test role — flowmeter armed,\n"
         " vent open+running is a precondition)",
         COL_WAIT_BG)
    node("ARMED",
         "ARMED\n(run spec validated & clamped,\nwaiting for confirm(runId))",
         COL_ARMED_BG)
    node("LEAKING",
         "LEAKING\n(flowmeter setpoint per run spec,\nintegrating inventory)",
         COL_LEAK_BG)
    node("HOLD",
         "HOLD\n(gas off, fans off —\nwatch propagation undisturbed)",
         COL_HOLD_BG)
    node("VENTILATING",
         "VENTILATING\n(registers per spec, fan speed per spec,\ntimed — if not fully vented by\ndeadline: escalate)",
         COL_VENT_BG)
    node("FULLYVENT",
         "FULLY-VENTILATING\n(all registers open, fan 100%,\ngas cut two ways: setpoint=0V AND relay open)\n"
         "serves 3 roles: mandatory end-of-run purge,\nemergency response, pre-experiment clean-air proof",
         COL_FULLVENT_BG, peripheries="2")

    # --- normal experiment flow ---
    oedge(g, "WAITING", "ARMED", "start(spec)\nvalidated & clamped",
          color=COL_NORMAL, penwidth="1.6")
    oedge(g, "ARMED", "LEAKING", "confirm(runId)\nmatches",
          color=COL_NORMAL, penwidth="1.6",
          tailport="sw", headport="e")
    oedge(g, "ARMED", "WAITING", "confirm timeout /\nmismatched runId",
          color=COL_NEUTRAL, style="dashed")
    oedge(g, "LEAKING", "HOLD", "stop condition met\n(type-B: max/mean/median/\ncount-above/timeout, OR'd)",
          color=COL_NORMAL, penwidth="1.6")
    oedge(g, "HOLD", "VENTILATING", "hold duration elapsed",
          color=COL_NORMAL, penwidth="1.6")
    oedge(g, "VENTILATING", "FULLYVENT", "mandatory purge\n(always, end of every run)",
          color=COL_NORMAL, penwidth="1.6")
    oedge(g, "FULLYVENT", "WAITING",
           "all-clear (local + peer)\nheld \u2265 5 min\n[auto — routine purge]",
          color=COL_NORMAL, penwidth="1.6")

    # --- equipment-test note ---
    # (was a WAITING->WAITING self-loop; splines="ortho" can't route those,
    #  so it's an explicit side node instead)
    g.node("SELFLIP", "selector flip\n(role switch,\ntakes effect immediately)",
           shape="plaintext", fontcolor=COL_NEUTRAL, fontsize="9")
    g.edge("WAITING", "SELFLIP", color=COL_NEUTRAL, style="dashed", arrowhead="none",
           constraint="false")
    g.edge("SELFLIP", "WAITING", color=COL_NEUTRAL, style="dashed", constraint="false")

    # --- emergency exit: from ANY state, straight to Fully-Ventilating ---
    # Five red edges run FROM each operating state INTO fully-venting. Under
    # rankdir=TB with all five sources stacked in the same left-hand column,
    # the plain lines used to run rightward off each box and merge into one
    # indistinguishable red bundle dropping into FULLYVENT — so each edge now
    # carries its own tag ("EXIT <state> → <why>"), placed AT ITS
    # SOURCE (xlabel anchors to the tail end here) rather than mid-line, and
    # each exits from a distinct port (e/se/s/sw/w) so the five lines fan out
    # instead of overlapping — the tag and the port together let a reader
    # name a line without tracing its full length.
    danger = [
        ("WAITING",     "sw", "e-stop / peer alarm / sensor"),
        ("ARMED",       "sw", "e-stop / peer alarm / sensor"),
        ("LEAKING",     "se", "leak / flow / inventory cap"),
        ("HOLD",        "s",  "e-stop / peer alarm / sensor"),
        ("VENTILATING", "sw", "escalation timeout"),
    ]
    danger_headports = {
        "WAITING":     "ne",
        "ARMED":       "ne",
        "LEAKING":     "n",
        "HOLD":        "nw",
        "VENTILATING": "nw",
    }
    for src, tailport, why in danger:
        oedge(g, src, "FULLYVENT",
              label=f"EXIT {src} → {why}",
              color=COL_DANGER, penwidth="2.2", style="bold",
              tag_border=True,
              tailport=tailport, headport=danger_headports[src],
              constraint="false" if src != "VENTILATING" else "true")

    with g.subgraph(name="cluster_triggers") as c:
        c.attr(label="Fully-Ventilating triggers (firmware-fixed, OR'd; from ANY state)",
               fontname=FONT, fontsize="10", color=COL_DANGER, style="dashed",
               fontcolor=COL_DANGER)
        c.node("TRIG",
               "\u2022 local sensor(s) above threshold\n"
               "  (max(firmware min, website value) — website only raises sensitivity)\n"
               "\u2022 flow > limit for > 2s  (leak-test mode only)\n"
               "\u2022 inventory cap exceeded  (leak-test mode only)\n"
               "\u2022 peer PLC alarm received (all modes, incl. equipment-test)\n"
               "\u2022 permit veto present & false (all modes)\n"
               "\u2022 physical e-stop button > 2.5V (all modes, no network needed)",
               shape="note", style="filled", fillcolor=COL_LEAK_BG, color=COL_DANGER,
               fontsize="9.5", align="left")
    g.edge("TRIG", "FULLYVENT", style="invis")

    oedge(g, "FULLYVENT", "WAITING",
          "all-clear ≥ 5 min\n+ human ack\n[after emergency]",
          color=COL_DANGER, penwidth="1.8", style="bold",
          constraint="false")

    return g


# ===========================================================================
# 2. SAFETY DATA FLOW  (one state machine -- no veto layer)
# ===========================================================================
def build_safety_dataflow():
    g = base_digraph("safety_dataflow", rankdir="LR")
    g.attr(label="Control Path \u2014 one state machine, one pin writer. "
                 "Danger is a TRANSITION, not a rewrite.",
           labelloc="t", fontsize="16", fontname=FONT + " Semibold")

    g.attr("node", shape="box", style="filled,rounded", color=INK, penwidth="1.3")

    g.node("SENSORS",
           "Sensors.h/.cpp\n\n"
           "local I1-I6 (H2) + base spares\n"
           "flow feedback (0-10V)\n"
           "selector position\n"
           "e-stop button\n"
           "peer alarms \u2014 PER ZONE (MQTT)\n"
           "permit heartbeat (MQTT)\n\n"
           "\u2192 builds SensorState\n"
           "(the one calibrated-read boundary)",
           fillcolor=COL_WAIT_BG)
    g.node("SSTATE",
           "SensorState\n"
           "{{ localSensors[], flow,\n"
           "  peerAlarmActive, permit,\n"
           "  estop, selector role }}",
           shape="note", fillcolor="#ffffff")
    g.node("CORE",
           "KitchenCore.h/.cpp\n\n"
           "update(sensors, now)\n\n"
           "1. dangerActive()  \u2190 FIRST, every pass\n"
           "2. \u2192 FULLY_VENTILATING from ANY state\n"
           "3. per-state logic \u2014 reads spec_ ONLY\n"
           "   when no danger is active\n\n"
           "THE AUDIT TARGET \u2014 pure, no Arduino",
           fillcolor=COL_LEAK_BG, color=COL_DANGER, penwidth="2.2")
    g.node("REQ",
           "OutputRequest\n"
           "{{ registers[3], fanSpeedPct,\n"
           "  gasOpen, gasSetpointPct,\n"
           "  alarmOn, sensorsOn }}\n\n"
           "a desired END STATE,\n"
           "not a sequence of actions",
           shape="note", fillcolor="#ffffff")
    g.node("OUTPUTS",
           "Outputs.h/.cpp\n\n"
           "drive(req, now)\n\n"
           "ONLY place that calls\n"
           "digitalWrite/analogWrite\n"
           "on these pins\n\n"
           "+ RegisterSequencer\n"
           "  (inlet-open delay)",
           fillcolor=COL_VENT_BG)
    g.node("HW",
           "Hardware\n\n"
           "3 vent registers (6 coils)\n"
           "fan speed DAC\n"
           "gas relay + setpoint DAC\n"
           "(two independent cuts)\n"
           "alarm relay + 4 status LEDs",
           shape="box3d", fillcolor="#e9e5db")
    g.node("SPEC",
           "RunSpec\n(from the website)\n\n"
           "DATA, never a command.\n"
           "Only ever READ by update(),\n"
           "and only in the LEAKING /\n"
           "HOLD / VENTILATING branches.",
           shape="note", fillcolor=COL_ARMED_BG)

    oedge(g, "SENSORS", "SSTATE", color=COL_NORMAL, penwidth="1.6")
    oedge(g, "SSTATE", "CORE", color=COL_NORMAL, penwidth="1.6")
    oedge(g, "CORE", "REQ", "update()", color=COL_NORMAL, penwidth="1.6")
    oedge(g, "REQ", "OUTPUTS", color=COL_NORMAL, penwidth="1.6")
    oedge(g, "OUTPUTS", "HW", "drive()", color=COL_NORMAL, penwidth="1.6")
    oedge(g, "SPEC", "CORE", "read, never obeyed",
          color=COL_NEUTRAL, style="dashed")

    with g.subgraph(name="cluster_note") as c:
        c.attr(label="", color="none")
        c.node("NOTE",
               "No veto layer, no latch object, no second source of truth.\n"
               "dangerActive() runs at the TOP of update(), before any per-state logic.\n"
               "If it holds, the machine transitions to FULLY_VENTILATING and update()\n"
               "returns that state's fixed outputs on the SAME PASS: gasOpen=false,\n"
               "setpoint=0V, all registers open, fan=100%, alarmOn=true.\n\n"
               "The website cannot reach the pins: the branches that read RunSpec are\n"
               "unreachable while a danger condition holds. State and pins never disagree.",
               shape="note", fillcolor=COL_ARMED_BG, color="#b7791f", fontsize="9.5")
    g.edge("CORE", "NOTE", style="invis")

    return g


# ===========================================================================
# 3. HARDWARE I/O MAP
# ===========================================================================
def build_hardware_map():
    g = base_digraph("hardware_io_map", rankdir="LR")
    g.attr(label="Hardware I/O Map — Opta base + A0602 analog exp. + D1608E relay exp.",
           labelloc="t", fontsize="16", fontname=FONT + " Semibold")

    g.attr("node", shape="record", style="filled", color=INK, penwidth="1.2",
           fillcolor="#ffffff")

    g.node("BASE_IN",
           "{ Opta base board — analog inputs (A0-A7, 0-10V) |"
           " { A0 | H2 sensor #7 (spare / future) } |"
           " { A1 | flow feedback (0-10V) } |"
           " { A2 | selector: \u22642.5V leak-test / \\>2.5V equip-test } |"
           " { A3 | e-stop button (0-5V \u2192 Fully-Vent) } |"
           " {A4-A7 | spare (future H2 sensors, up to 14 total) } }")

    g.node("EXP_IN",
           "{ A0602 analog expansion — inputs I1-I6 |"
           " {I1 | H2 sensor 1 } | {I2 | H2 sensor 2 } | {I3 | H2 sensor 3 } |"
           " {I4 | H2 sensor 4 } | {I5 | H2 sensor 5 } | {I6 | H2 sensor 6 } }")

    g.node("EXP_OUT",
           "{ A0602 analog expansion — outputs |"
           " {O1 | fan speed setpoint (0-10V) } |"
           " {O2 | flowmeter setpoint (0-10V) } }")

    g.node("RELAYS",
           "{ D1608E relay expansion — all 8 used\\n"
           "written ONLY via Outputs::drive() |"
           " {CH0/CH1 | CENTRAL register — open / close } |"
           " {CH2/CH3 | EXHAUST register — open / close } |"
           " {CH4/CH5 | INLET register — open / close\\n(opens 1s AFTER co-opened registers) } |"
           " {CH6 | gas supply relay — cut #1 } |"
           " {CH7 | alarm beacon } }")

    g.node("RELAYNOTE",
           "No dedicated flowmeter-cut relay: the D1608E has 8 channels\\n"
           "and all 8 are used above. The SECOND independent gas cut is\\n"
           "O2 driven to 0 V, not a relay. The fan contactor follows the\\n"
           "O1 speed DAC (0 V = off). A 2nd D1608E would free a channel\\n"
           "for a hardware flowmeter cut if one is wired later.",
           shape="note", fillcolor=COL_ARMED_BG, color="#b7791f", fontsize="9")

    g.node("LEDS",
           "{ Status LEDs — all four as ONE 2+2 word (Outputs is sole writer) |"
           " {D1 D0 — connection | 00 no link · 01 link, no MQTT · 11 MQTT up\\n"
           "blinking = that layer WAS up and dropped } |"
           " {D3 D2 — run state | 00 WAITING/ARMED · 01 LEAKING/equip-test\\n"
           "10 HOLD · 11 VENTILATING / FULLY-VENT } |"
           " {all 4 fast-blink | ALARM (200ms) — overrides everything } |"
           " {D3/D2 slow-blink | stale peer (800ms) — warn only,\\nconnection bits stay normal } }")

    g.node("MCU", "OPTA CM7\n(this firmware)", shape="box", style="filled,rounded",
           fillcolor=COL_ARMED_BG, fontsize="12")

    g.edge("BASE_IN", "MCU", color=COL_NORMAL)
    g.edge("EXP_IN", "MCU", color=COL_NORMAL)
    g.edge("MCU", "EXP_OUT", color=COL_NORMAL)
    # The MCU->RELAYS hop is too short to caption from either end without the
    # text landing on a node, so the "safety-filtered" note lives in the
    # RELAYS block header instead (see the record label above).
    g.edge("MCU", "RELAYS", color=COL_DANGER, penwidth="1.6")
    g.edge("RELAYS", "RELAYNOTE", style="invis")
    g.edge("MCU", "LEDS", color=COL_NEUTRAL)

    return g


# ===========================================================================
# 4. MQTT TOPIC MAP
# ===========================================================================
def build_mqtt_map():
    g = base_digraph("mqtt_topic_map", rankdir="LR")
    g.attr(label="MQTT Topic Map — Kitchen PLC \u2194 broker \u2194 server.py / centralPLC / website",
           labelloc="t", fontsize="16", fontname=FONT + " Semibold")

    g.attr("node", shape="box", style="filled,rounded", color=INK, penwidth="1.3")

    g.node("WEBSITE", "Website\n(not yet built)", fillcolor=COL_HOLD_BG)
    g.node("KITCHEN", "Kitchen PLC\n(this firmware)", fillcolor=COL_ARMED_BG)
    g.node("BROKER", "MQTT Broker", shape="ellipse", fillcolor="#ffffff")
    g.node("SERVER", "server.py\n(Central Safety bridge\n\u2192 Firebase)", fillcolor=COL_WAIT_BG)
    g.node("PEERS", "Peer experiment PLCs\n(Turbine, others)", fillcolor="#e3e0d8")
    # Data-acquisition PLCs: publish measured H2 concentration to the broker.
    # Distinct from PEERS — these report readings, they don't raise interlocks.
    g.node("ACQ", "Acquisition PLCs (DataAcquisition/CM7)\n"
                  "OPTA — analog + pulse sensors\n"
                  "DEVICE_LOCATION = Turbine | Kitchen | Other",
           shape="box", style="filled,rounded", fillcolor=COL_VENT_BG,
           color=COL_SENSOR, penwidth="1.6")

    # Topic text lives in its own node on each channel rather than as an edge
    # label: six labelled edges between KITCHEN and BROKER would pile their
    # xlabels into one illegible heap under ortho routing. Each topic node is
    # a waypoint, so the lines stay straight and the text stays put.
    def topic(name, text, color, fill="#ffffff"):
        g.node(name, text, shape="box", style="filled", fillcolor=fill,
               color=color, penwidth="1.2", fontsize="9.5", fontname=FONT_MONO)

    topic("T_CMD",
          "KitchenControl/{id}/cmd\l"
          "  start spec / confirm(runId) / stop\l"
          "  config/set thresholds\l"
          "[NEVER retained]\l", COL_NORMAL)
    topic("T_TELEM",
          "KitchenControl/{id}/ack     validated spec / rejection\l"
          "KitchenControl/{id}/state   retained: mode, phase,\l"
          "                            selector role, elapsed\l"
          "DataAcquisition/Kitchen/{id}/*   sensor samples\l"
          "KitchenControl/{id}/config/ack   clamped thresholds\l", COL_NORMAL)
    topic("T_RUN",
          "status/{ExperimentName}/{id}/run\l"
          "  {running, runId}\l"
          "status/{ExperimentName}/{id}/online\l"
          "  (LWT, retained)\l", COL_NEUTRAL)
    topic("T_ALARM",
          "status/{ExperimentName}/{id}/alarm/{labId}/hydrogen\l"
          "  {active, locationDetail, description}\l"
          "[on Fully-Vent entry from danger/alarm]\l",
          COL_DANGER, fill=COL_LEAK_BG)
    topic("T_PERMIT",
          "safety/permit/{id}   {permit: bool, seq: N}\l"
          "  ~1s heartbeat, NOT retained\l"
          "  veto when present; absence → warn only\l", COL_NEUTRAL)
    topic("T_PEER",
          "status/{ExperimentName}/{id}/alarm/{labId}/{hazard}\l"
          "  (retained)\l"
          "  peer alarm → primary interlock\l"
          "  (direct, survives server.py dying)\l",
          COL_DANGER, fill=COL_LEAK_BG)
    # Real contract, from DataAcquisition/CM7 (CM7.ino publishVoltage/Current/Pwm
    # + Comms.cpp commsBegin). One topic PER SENSOR, named by the sensor's
    # dashboard-assigned name — there is no hydrogen-specific topic, and no
    # concentration field: the PLC publishes raw volts and the dashboard applies
    # the conversion. deviceId = "{MAC24}-{DEVICE_LOCATION}", e.g. 474D0C-Kitchen.
    topic("T_ACQ",
          "DataAcquisition/{Location}/{deviceId}/{sensorName}\l"
          "  {\"pin\":N,\"type\":\"voltage\",\"raw_v\":F,\"ts\":ms}\l"
          "  (also type \"current\" → raw_ma,\l"
          "   type \"pwm\" → avg_period_us + pulse_count)\l"
          "  every activeIntervalMs (default 500 ms), NOT retained\l"
          "  raw ADC only — ppm/%LEL conversion is dashboard-side\l",
          COL_SENSOR, fill=COL_VENT_BG)
    topic("T_ACQ_CFG",
          "DataAcquisition/{Location}/{deviceId}/status\l"
          "  \"online\" / \"offline\"  (LWT, RETAINED)\l"
          "config/get → config/ack   {device_name, sensors[], interval}\l"
          "config/set ← dashboard    (sole producer)\l",
          COL_NEUTRAL)

    # website -> broker -> kitchen  (commands in)
    g.edge("WEBSITE", "T_CMD", color=COL_NORMAL, penwidth="1.4", arrowhead="none")
    g.edge("T_CMD", "BROKER", color=COL_NORMAL, penwidth="1.4")
    # kitchen -> broker -> website  (acks + telemetry out)
    g.edge("KITCHEN", "T_TELEM", color=COL_NORMAL, penwidth="1.4", arrowhead="none")
    g.edge("T_TELEM", "BROKER", color=COL_NORMAL, penwidth="1.4")

    # kitchen -> broker -> server.py  (run status, LWT, hydrogen alarm)
    g.edge("KITCHEN", "T_RUN", color=COL_NEUTRAL, arrowhead="none")
    g.edge("T_RUN", "BROKER", color=COL_NEUTRAL)
    g.edge("KITCHEN", "T_ALARM", color=COL_DANGER, penwidth="1.8", arrowhead="none")
    g.edge("T_ALARM", "BROKER", color=COL_DANGER, penwidth="1.8")

    # server.py -> broker -> kitchen  (permit heartbeat / veto)
    g.edge("SERVER", "T_PERMIT", color=COL_NEUTRAL, style="dashed", arrowhead="none")
    g.edge("T_PERMIT", "BROKER", color=COL_NEUTRAL, style="dashed")

    # peers -> broker -> kitchen  (peer alarm interlock)
    g.edge("PEERS", "T_PEER", color=COL_DANGER, penwidth="1.8", arrowhead="none")
    g.edge("T_PEER", "BROKER", color=COL_DANGER, penwidth="1.8")

    # acquisition PLCs -> broker  (per-sensor sample stream + status/config)
    g.edge("ACQ", "T_ACQ", color=COL_SENSOR, penwidth="1.6", arrowhead="none")
    g.edge("T_ACQ", "BROKER", color=COL_SENSOR, penwidth="1.6")
    g.edge("ACQ", "T_ACQ_CFG", color=COL_NEUTRAL, style="dashed", arrowhead="none")
    g.edge("T_ACQ_CFG", "BROKER", color=COL_NEUTRAL, style="dashed", dir="both")

    # broker fan-out to the subscribers
    g.edge("BROKER", "KITCHEN", color=COL_NORMAL, penwidth="1.4")
    g.edge("BROKER", "WEBSITE", color=COL_NORMAL, penwidth="1.4")
    g.edge("BROKER", "SERVER", color=COL_NEUTRAL)

    # Legend as an HTML-like table: each row pairs an actual coloured line
    # sample with dark body text, so the key reads against white instead of
    # relying on low-contrast coloured type.
    def key_row(color, text, dash=""):
        return (
            f'<TR>'
            f'<TD FIXEDSIZE="TRUE" WIDTH="34" HEIGHT="16" BGCOLOR="{color}"></TD>'
            f'<TD ALIGN="LEFT"><FONT COLOR="{INK}" POINT-SIZE="10">'
            f'{text}{dash}</FONT></TD>'
            f'</TR>'
        )

    legend_html = (
        '<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="6" CELLPADDING="2">'
        '<TR><TD COLSPAN="2" ALIGN="LEFT">'
        '<FONT COLOR="' + INK + '" POINT-SIZE="11"><B>Legend</B></FONT>'
        '</TD></TR>'
        + key_row(COL_DANGER, "safety-critical / emergency path")
        + key_row(COL_NORMAL, "normal command / telemetry path")
        + key_row(COL_NEUTRAL, "advisory / bookkeeping path")
        + key_row(COL_SENSOR, "raw sensor sample stream (DAQ PLCs)")
        + '</TABLE>>'
    )

    with g.subgraph(name="cluster_legend") as c:
        c.attr(label="", color=COL_NEUTRAL, penwidth="1.2", style="rounded",
               bgcolor="#ffffff")
        c.node("LEGEND", legend_html, shape="plaintext", fillcolor="transparent",
               style="")

    return g


# ===========================================================================
if __name__ == "__main__":
    diagrams = {
        "state_machine":   build_state_machine,
        "safety_dataflow": build_safety_dataflow,
        "hardware_io_map": build_hardware_map,
        "mqtt_topic_map":  build_mqtt_map,
    }
    only = sys.argv[1:] if len(sys.argv) > 1 else diagrams.keys()
    for key in only:
        if key not in diagrams:
            print(f"unknown diagram: {key}  (choices: {list(diagrams)})")
            continue
        print(f"--- {key} ---")
        g = diagrams[key]()
        render(g, key)
    print("\nDone.")
