#!/usr/bin/env python3
"""Concept deck for sts-echo-example. Content only — the slide machinery lives in
the shared toolkit at ../../ppt-tools/veeam_deck.py.

    python3 ppt-tools/build.py sts-echo-example     # from the repo root
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck  # noqa: E402

d = Deck('sts-echo', 'sts-echo-example')

d.title("THE KANISTER TEMPLATE\nCONTEXT, EXPLAINED",
        "A demo blueprint that moves no data and only prints labels")

d.agenda([
    "What this blueprint is for",
    "The template context Kasten passes in",
    "What the blueprint does",
    "Try it yourself",
])

d.hero(
    "A BLUEPRINT THAT", "MOVES NO DATA AT ALL",
    body="It exists to show one thing: what is inside the template context when "
         "Kasten invokes a blueprint,\nand how to walk it.",
    pills=["Teaching aid", "No PVCs touched", "Safe to run anywhere"],
    icon='question_help')

d.cards(
    "Why a blueprint that does nothing is useful",
    "Template variables are the single most common thing to get wrong in a blueprint",
    [
        ('search_eye', "THE CONTEXT IS INVISIBLE", [
            "When a blueprint misbehaves, the usual cause is a template variable that "
            "resolved to an empty string rather than a phase that failed.",
            "",
            "Nothing tells you this happened. The phase runs, succeeds, and does the "
            "wrong thing quietly.",
        ]),
        ('function_add', "SO PRINT IT INSTEAD", [
            "This blueprint ranges over .Object.metadata.labels and echoes every key "
            "and value it finds.",
            "",
            "The rendered script appears in the Kanister log, so you can read exactly "
            "what Kasten handed the blueprint.",
        ]),
        ('checkbox_multiple', "THEN BUILD ON IT", [
            "Once you can see the context, the same walk works for annotations, the "
            "object name, the namespace, and the rest of .Object.",
            "",
            "The same blueprint is provided bound to a MariaDB operator resource, to "
            "show the context changes with the bound object.",
        ]),
    ])

d.flow(
    "How the label walk happens",
    "The annotation sits on the StatefulSet itself — no separate annotate step is needed",
    [
        ('file_list', "ANNOTATED STATEFULSET",
         "kanister.kasten.io/blueprint:\nconfigmap-echo\n\nCarries labels app, env\nand tier"),
        ('loop_left', "KASTEN CALLS backupPrehook",
         "Kasten binds the StatefulSet\nto the blueprint and passes\nthe object in as\n.Object"),
        ('function_add', "THE TEMPLATE RENDERS",
         "range $key, $value over\n.Object.metadata.labels\nbecomes one echo line\nper label"),
        ('window_browser', "THE LOG SHOWS THE RESULT",
         "Label: app=demo\nLabel: env=test\nLabel: tier=backend"),
    ],
    note=("Read the rendered script, not the template.",
          "The value of this blueprint is that the Kanister log contains the script "
          "after rendering. A variable that resolved to nothing shows up as a missing "
          "or empty line, which is exactly the failure that is otherwise silent."))

d.table_slide(
    "Blueprint action",
    "One action, one KubeTask. Deploy the blueprint in kasten-io.",
    ["Action", "Function", "What it does"],
    [
        ("backupPrehook", "KubeTask",
         "Runs a shell script rendered from a Go template that ranges over "
         ".Object.metadata.labels and echoes each key and value. Touches no volume "
         "and writes nothing."),
    ],
    col_widths=[2.3, 2.15, 7.85],
    row_heights=[0.5, 1.1],
    cards=[
        ('sparkling_magic', "WHAT TO COPY FROM IT", [
            "The range construct, and the habit of echoing the context before you act "
            "on it. When a real blueprint goes wrong, adding one echo of the value you "
            "depend on usually finds the cause faster than reading the executor log."]),
        ('close_circle', "WHAT NOT TO COPY FROM IT", [
            "This is not a backup. There is no data movement, no quiesce and no "
            "restore action, and a policy using it protects nothing. It is a "
            "demonstration of the template context and nothing more."]),
    ])

d.steps_dark(
    "Try it", "Three steps, then read the log",
    [
        ('settings_gear', "DEPLOY THE BLUEPRINT",
         "kubectl apply -f blueprint.yaml puts the blueprint in kasten-io."),
        ('rocket_launch', "CREATE THE TEST STATEFULSET",
         "The manifest in the README creates namespace sts-echo-test and a StatefulSet "
         "already carrying the blueprint annotation and the three labels."),
        ('upload_cloud', "RUN A BACKUP",
         "Trigger a Kasten policy against sts-echo-test so the prehook fires."),
        ('search_eye', "READ THE KANISTER LOG",
         "kubectl logs -n kasten-io -l app=kanister-svc shows the rendered script and "
         "one Label: line per label on the StatefulSet."),
    ])

d.ending("SEE THE CONTEXT BEFORE YOU TRUST IT",
         "A template variable that resolves to nothing fails silently.\n"
         "github.com/kastenhq/blueprint-ai — sts-echo-example")

d.save()
